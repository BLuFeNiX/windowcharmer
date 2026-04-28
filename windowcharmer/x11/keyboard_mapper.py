import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass

from Xlib import XK, X
from Xlib.display import Display
from Xlib.ext import xtest

from .display_pool import DisplayPool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KeysymPositions:
    """Where Super_L and Hyper_L keysyms currently live in the X11 keymap.

    A keycode here means: the position at which a press produces this
    keysym at shift level 0 — falling back to "any level" only when
    level 0 doesn't match. See `scan_keymap_positions` for the rationale.

    The `physical_*` properties expose the xkb-conventional physical key
    positions (lower keycode = Super, higher = Hyper). These are *stable
    across our own swap*: min/max is invariant under swapping the two
    values, so callers that need to track the physical Super key (e.g.
    the bare-tap passthrough) get a value that doesn't move when the
    keysyms are exchanged.
    """

    super_l_kc: int
    hyper_l_kc: int

    @property
    def physical_super_kc(self) -> int:
        return min(self.super_l_kc, self.hyper_l_kc)

    @property
    def physical_hyper_kc(self) -> int:
        return max(self.super_l_kc, self.hyper_l_kc)

    @property
    def both_present(self) -> bool:
        return bool(self.super_l_kc and self.hyper_l_kc)


def scan_keymap_positions(dpy: Display, super_keysym: int, hyper_keysym: int) -> KeysymPositions:
    """Scan the live X server keymap and return current positions of both keysyms.

    Pure function: reads via get_keyboard_mapping but mutates nothing. Returns
    a snapshot the caller can reason about as a single value.

    Two-pass scan, level 0 preferred:

    Pass 1 matches `keysyms[0]` only — the level-0 keysym is what an unmodified
    keypress produces, so this is the keycode the keysym actually "lives" at.
    Crucially, it ignores residual matches at higher levels: the swap's
    preserve-higher-levels write semantics intentionally leave the previous
    keysym at level 1+ of the rewritten keycode, and an "any-level" search
    would find both keysyms there and collapse them onto the same keycode.

    Pass 2 falls back to "any level" only if level 0 didn't yield a match.
    Covers layouts where a modifier is bound only at a non-zero shift level
    on a fresh canonical keymap (xkb US:
    `keycode 207 = NoSymbol Hyper_L NoSymbol Hyper_L`).

    Returns kc=0 for any keysym not found.
    """
    info = dpy.display.info
    kc_min = info.min_keycode
    count = info.max_keycode - kc_min + 1
    mapping = dpy.get_keyboard_mapping(kc_min, count)

    super_kc = 0
    hyper_kc = 0

    # Pass 1: prefer the keycode where the keysym is at level 0.
    for offset, keysyms in enumerate(mapping):
        if not keysyms:
            continue
        kc = kc_min + offset
        if not super_kc and keysyms[0] == super_keysym:
            super_kc = kc
        if not hyper_kc and keysyms[0] == hyper_keysym:
            hyper_kc = kc
        if super_kc and hyper_kc:
            return KeysymPositions(super_l_kc=super_kc, hyper_l_kc=hyper_kc)

    # Pass 2: any level (only for keysyms missed by pass 1).
    for offset, keysyms in enumerate(mapping):
        if not keysyms:
            continue
        kc = kc_min + offset
        if not super_kc and super_keysym in keysyms:
            super_kc = kc
        if not hyper_kc and hyper_keysym in keysyms:
            hyper_kc = kc
        if super_kc and hyper_kc:
            break

    return KeysymPositions(super_l_kc=super_kc, hyper_l_kc=hyper_kc)


@dataclass(frozen=True)
class _Backup:
    """Captured-once state used to restore the keymap on cleanup.

    Holds the original rows from the canonical positions at startup, plus
    those positions themselves so cleanup writes to the right keycodes
    even if scan_keymap_positions would now report different ones (e.g.
    after we've applied our swap).
    """

    canon_super_kc: int
    canon_hyper_kc: int
    super_row: Sequence[Sequence[int]]
    hyper_row: Sequence[Sequence[int]]


class KeyboardMapper:
    """Manages the Super_L ↔ Hyper_L keymap swap and key event simulation.

    State invariants:
    - `_backup` is captured once at __init__ and never mutated afterward.
      It records what cleanup() should write to restore the keymap.
    - All other operations re-scan the live keymap each call (via
      `scan_keymap_positions`) and reason about a fresh snapshot — there
      is no mutable "current position" cache on self that can drift.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dpy: Display = DisplayPool.get_display("mapper")

        self._super_l_keysym: int = XK.string_to_keysym("Super_L")
        self._hyper_l_keysym: int = XK.string_to_keysym("Hyper_L")

        self._backup: _Backup | None = self._capture_backup()

    def _capture_backup(self) -> _Backup | None:
        """Snapshot canonical-position rows for cleanup. Detect post-crash
        inverted state and synthesize minimal-row originals for that case.

        After a previous run crashed mid-swap the canonical Super position
        holds Hyper_L. We don't have the original level-1+ bindings from
        that pre-crash state, so synthesize a single-keysym restore — better
        to lose level-1+ customizations on this one keycode than leave the
        keymap permanently inverted.
        """
        try:
            positions = scan_keymap_positions(self._dpy, self._super_l_keysym, self._hyper_l_keysym)
            if not positions.both_present:
                logger.warning(
                    "Super_L (kc=%d) or Hyper_L (kc=%d) not found in current keymap — "
                    "keymap swap and bare-Super passthrough will be disabled.",
                    positions.super_l_kc,
                    positions.hyper_l_kc,
                )
                return None

            canon_super_kc = positions.physical_super_kc
            canon_hyper_kc = positions.physical_hyper_kc
            canon_super_row = self._dpy.get_keyboard_mapping(canon_super_kc, 1)
            is_inverted = (
                canon_super_row
                and canon_super_row[0]
                and canon_super_row[0][0] == self._hyper_l_keysym
            )
            if is_inverted:
                return _Backup(
                    canon_super_kc=canon_super_kc,
                    canon_hyper_kc=canon_hyper_kc,
                    super_row=[[self._super_l_keysym]],
                    hyper_row=[[self._hyper_l_keysym]],
                )
            return _Backup(
                canon_super_kc=canon_super_kc,
                canon_hyper_kc=canon_hyper_kc,
                super_row=canon_super_row,
                hyper_row=self._dpy.get_keyboard_mapping(canon_hyper_kc, 1),
            )
        except Exception as e:
            logger.error("Error backing up key mappings: %s", e)
            logger.debug("", exc_info=True)
            return None

    def physical_super_kc(self) -> int:
        """Return the physical Super key keycode from a fresh keymap scan.

        This is the canonical lower keycode (xkb convention), stable across
        our own swap. Use this — not "where the Super_L keysym lives now" —
        to detect bare physical Super presses, since post-swap the keysym
        moves but the physical key doesn't.

        Returns 0 if neither keysym is present in the keymap.
        """
        with self._lock:
            return scan_keymap_positions(
                self._dpy, self._super_l_keysym, self._hyper_l_keysym
            ).physical_super_kc

    def apply_super_hyper_swap(self) -> None:
        """Swap Super_L and Hyper_L keysyms at level 0 of their canonical positions.

        Idempotent across self-triggered MappingNotify echoes — every
        change_keyboard_mapping call causes the X server to broadcast
        MappingNotify, which the InputManager routes back to this method.
        The idempotency check has to survive seeing the layout we just wrote.

        Each call re-scans the live keymap, so a mid-session keymap change
        (setxkbmap, custom Xkb layouts) that moves Super_L or Hyper_L to
        different keycodes redirects the swap to the new positions rather
        than clobbering whatever now lives at the stale canonical position.
        The positional convention (lower keycode = canonical Super) keeps
        physical positions stable across our own swap echoes, so the
        idempotency check still reads our prior write and short-circuits.
        """
        with self._lock:
            try:
                if self._backup is None:
                    return
                positions = scan_keymap_positions(self._dpy, self._super_l_keysym, self._hyper_l_keysym)
                if not positions.both_present:
                    logger.debug("Super_L or Hyper_L missing from current keymap — skipping swap.")
                    return

                canon_super_kc = positions.physical_super_kc
                canon_hyper_kc = positions.physical_hyper_kc

                # Read the keysym at the canonical Super position. Pre-swap it
                # holds Super_L; post-swap it holds Hyper_L. Using the canonical
                # (positionally stable) keycode here is what makes this
                # idempotent — refreshing-then-checking on super_l_keycode
                # would read back our own write and conclude "not yet swapped"
                # forever.
                current_map = self._dpy.get_keyboard_mapping(canon_super_kc, 1)
                already_swapped = (
                    current_map
                    and len(current_map) > 0
                    and len(current_map[0]) > 0
                    and current_map[0][0] == self._hyper_l_keysym
                )
                if already_swapped:
                    logger.debug("Super_L already mapped to Hyper_L — no action needed.")
                    return

                logger.info("Swapping Super_L and Hyper_L...")
                self._set_level0(canon_super_kc, self._hyper_l_keysym)
                self._set_level0(canon_hyper_kc, self._super_l_keysym)
                self._dpy.sync()
            except Exception as e:
                logger.error("Error rebinding keys: %s", e)
                logger.debug("", exc_info=True)

    def force_canonical(self) -> bool:
        """Restore canonical mapping (lower keycode→Super_L, higher→Hyper_L) without the swap logic.

        Returns False if either keysym is absent from the current mapping.
        Scans the live keymap directly so it sees mid-session changes from
        setxkbmap and similar.
        """
        with self._lock:
            positions = scan_keymap_positions(self._dpy, self._super_l_keysym, self._hyper_l_keysym)
            if not positions.both_present:
                return False
            self._set_level0(positions.physical_super_kc, self._super_l_keysym)
            self._set_level0(positions.physical_hyper_kc, self._hyper_l_keysym)
            self._dpy.sync()
            return True

    def simulate_super_press(self) -> None:
        """Synthesize a Super_L key press+release for the bare-tap passthrough.

        The daemon's keymap swap puts Super_L's keysym at the canonical Hyper
        position (the higher of the two physical keycodes). Faking input there
        produces a Super_L keysym event from the X server's perspective — and
        the user's DE typically binds 'show application menu' to bare Super_L
        (Cinnamon's default), so this is what triggers the menu.

        We deliberately do NOT fake at where Hyper_L currently lives (the
        physical Super key's keycode post-swap). That would generate a Hyper_L
        event, which doesn't match the default Super_L binding.

        Misleadingly, this function used to be named simulate_hyper_press —
        but the historical behavior (when it worked) was always to fake at
        the post-swap-Super position, producing a Super_L event. The name
        referred to the keycode's PRE-SWAP keysym, not what the synthetic
        event produces.
        """
        with self._lock:
            try:
                positions = scan_keymap_positions(self._dpy, self._super_l_keysym, self._hyper_l_keysym)
                if not positions.super_l_kc:
                    logger.warning("Super_L not in current keymap — cannot simulate")
                    return
                row = self._dpy.get_keyboard_mapping(positions.super_l_kc, 1)
                level0 = row[0][0] if row and row[0] else 0
                logger.debug(
                    "simulate_super_press: faking at kc=%d, level0=0x%x (Super_L=0x%x)",
                    positions.super_l_kc,
                    level0,
                    self._super_l_keysym,
                )
                xtest.fake_input(self._dpy, X.KeyPress, positions.super_l_kc)
                xtest.fake_input(self._dpy, X.KeyRelease, positions.super_l_kc)
                self._dpy.flush()
            except Exception as e:
                logger.error("Error simulating key: %s", e)
                logger.debug("", exc_info=True)

    def _set_level0(self, keycode: int, new_keysym: int) -> None:
        """Set the level-0 keysym at keycode to new_keysym, preserving levels 1+.

        Custom xkb layouts often bind modifier keysyms at multiple shift levels
        (e.g. `keycode 207 = NoSymbol Hyper_L NoSymbol Hyper_L` so Hyper still
        works while Shift is held). A naive single-keysym write would truncate
        those higher levels — and since cleanup() only restores them on a clean
        shutdown, --fix-keymap would leave a custom layout permanently stripped.
        """
        existing = self._dpy.get_keyboard_mapping(keycode, 1)
        if existing and existing[0]:
            new_row = (new_keysym, *existing[0][1:])
        else:
            new_row = (new_keysym,)
        self._dpy.change_keyboard_mapping(keycode, [new_row])

    def cleanup(self) -> None:
        """Restore the original keysym assignments at the canonical positions
        captured at __init__.

        Uses the stored backup positions, NOT a fresh scan. This means a
        cleanup after setxkbmap moved keysyms mid-session writes the original
        rows back at the original keycodes — preserving the data/keycode pairing
        rather than risking writing kc 133's old data at a setxkbmap-relocated
        kc 100.
        """
        logger.info("Restoring keyboard mapping...")
        with self._lock:
            try:
                if self._backup is None:
                    logger.warning(
                        "Original key mappings unavailable — keyboard mapping not restored "
                        "(backup failed at startup)."
                    )
                    return
                self._dpy.change_keyboard_mapping(self._backup.canon_super_kc, self._backup.super_row)
                self._dpy.change_keyboard_mapping(self._backup.canon_hyper_kc, self._backup.hyper_row)
                self._dpy.sync()
            except Exception as e:
                logger.warning("Error restoring keyboard mapping: %s", e)
                logger.debug("", exc_info=True)
