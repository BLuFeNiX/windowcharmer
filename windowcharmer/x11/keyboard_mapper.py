import logging
import threading

from Xlib import XK, X
from Xlib.display import Display
from Xlib.ext import xtest

from .display_pool import DisplayPool

logger = logging.getLogger(__name__)


class KeyboardMapper:
    """Manages the Super_L ↔ Hyper_L keymap swap and key event simulation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dpy: Display = DisplayPool.get_display("mapper")

        self.super_l_keysym: int = XK.string_to_keysym("Super_L")
        self.hyper_l_keysym: int = XK.string_to_keysym("Hyper_L")

        self.super_l_keycode: int = self._dpy.keysym_to_keycode(self.super_l_keysym)
        self.hyper_l_keycode: int = self._dpy.keysym_to_keycode(self.hyper_l_keysym)

        # Canonical (lower=Super, higher=Hyper) positions captured once at
        # backup time. The swap and cleanup operate on these *stable* keycodes,
        # not on the runtime-tracked super_l_keycode/hyper_l_keycode pair —
        # otherwise the post-swap MappingNotify echo (which the KeyGrabber
        # routes back to apply_super_hyper_swap) would see runtime trackers
        # already pointing at the swapped positions and re-invert the mapping
        # forever.
        self._canon_super_kc: int = 0
        self._canon_hyper_kc: int = 0

        self.super_l_orig: list[list[int]] | None = None
        self.hyper_l_orig: list[list[int]] | None = None

        self._backup_mappings()

    def _backup_mappings(self) -> None:
        """Snapshot the current keysym assignments so cleanup() can restore them.

        After a previous run crashed mid-swap, keysym_to_keycode(Super_L) returns
        the keycode currently holding Super_L — i.e. the canonical Hyper position.
        Detection of the inverted state therefore can't trust self.super_l_keycode;
        it works off the canonically-Super position (the lower of the two keycodes,
        by X11 convention) and reads the keysym there. If Hyper_L is found, the
        mapping is inverted and we re-canonicalize the cached keycodes so cleanup
        and subsequent swap operations restore the canonical layout instead of
        cementing the inverted one.

        Uses _refresh_keycodes_locked rather than the __init__-time
        keysym_to_keycode values so canon_*_kc derived here matches what
        apply_super_hyper_swap will derive later — otherwise cleanup could
        write rows to the wrong keycodes if the two paths disagree.
        """
        try:
            self._refresh_keycodes_locked()
            if not self.super_l_keycode or not self.hyper_l_keycode:
                logger.warning(
                    "Super_L (kc=%d) or Hyper_L (kc=%d) not found in current keymap — "
                    "keymap swap and bare-Super passthrough will be disabled.",
                    self.super_l_keycode,
                    self.hyper_l_keycode,
                )
                return
            canon_super_map = self._dpy.get_keyboard_mapping(self._canon_super_kc, 1)
            inverted = canon_super_map and canon_super_map[0] and canon_super_map[0][0] == self.hyper_l_keysym
            if inverted:
                self.super_l_keycode = self._canon_super_kc
                self.hyper_l_keycode = self._canon_hyper_kc
                self.super_l_orig = [[self.super_l_keysym]]
                self.hyper_l_orig = [[self.hyper_l_keysym]]
            else:
                self.super_l_orig = self._dpy.get_keyboard_mapping(self._canon_super_kc, 1)
                self.hyper_l_orig = self._dpy.get_keyboard_mapping(self._canon_hyper_kc, 1)
        except Exception as e:
            logger.error("Error backing up key mappings: %s", e)
            logger.debug("", exc_info=True)

    def _refresh_keycodes_locked(self) -> None:
        """Re-fetch keycodes by scanning the live server mapping. Caller holds self._lock.

        keysym_to_keycode() uses Xlib's internal cache, which is only updated
        via refresh_keyboard_mapping() on that specific Display. Since the mapper
        Display never sees MappingNotify events, querying get_keyboard_mapping()
        directly is the only way to get the current server state.

        Two-pass scan, level 0 preferred:

        Pass 1 matches keysyms[0] only. The level-0 keysym is what an
        unmodified keypress produces, so this is the keycode the keysym
        actually "lives" at. Crucially, it ignores residual matches at
        higher levels — the swap's preserve-higher-levels semantics
        intentionally leaves the previous keysym at level 1+ of the
        rewritten keycode, and an "any level" search would find both
        keysyms there and collapse them onto the same keycode. That
        collapse made apply_super_hyper_swap's idempotency check
        succeed at the wrong canonical position and made cleanup write
        both originals to the same keycode, destroying the keymap.

        Pass 2 falls back to "any level" only if level 0 didn't yield a
        match. This preserves the original 84239cc fix for layouts where
        a modifier is bound only at level 1 (e.g. xkb US:
        `keycode 207 = NoSymbol Hyper_L NoSymbol Hyper_L`) so the daemon
        still detects Hyper_L's keycode on a fresh canonical keymap.

        Updates _canon_super_kc / _canon_hyper_kc as a side-effect so
        every caller sees consistent canonical positions without each
        having to re-derive min/max.
        """
        info = self._dpy.display.info
        kc_min, count = info.min_keycode, info.max_keycode - info.min_keycode + 1
        mapping = self._dpy.get_keyboard_mapping(kc_min, count)
        self.super_l_keycode = 0
        self.hyper_l_keycode = 0

        # Pass 1: prefer the keycode where the keysym is at level 0.
        for offset, keysyms in enumerate(mapping):
            if not keysyms:
                continue
            kc = kc_min + offset
            if not self.super_l_keycode and keysyms[0] == self.super_l_keysym:
                self.super_l_keycode = kc
            if not self.hyper_l_keycode and keysyms[0] == self.hyper_l_keysym:
                self.hyper_l_keycode = kc
            if self.super_l_keycode and self.hyper_l_keycode:
                break

        # Pass 2: fall back to any shift level for keysyms not found at level 0.
        if not self.super_l_keycode or not self.hyper_l_keycode:
            for offset, keysyms in enumerate(mapping):
                if not keysyms:
                    continue
                kc = kc_min + offset
                if not self.super_l_keycode and self.super_l_keysym in keysyms:
                    self.super_l_keycode = kc
                if not self.hyper_l_keycode and self.hyper_l_keysym in keysyms:
                    self.hyper_l_keycode = kc
                if self.super_l_keycode and self.hyper_l_keycode:
                    break

        if self.super_l_keycode and self.hyper_l_keycode:
            self._canon_super_kc = min(self.super_l_keycode, self.hyper_l_keycode)
            self._canon_hyper_kc = max(self.super_l_keycode, self.hyper_l_keycode)
        logger.debug(
            "Refreshed keycodes: Super_L=%d, Hyper_L=%d (canon Super kc=%d, canon Hyper kc=%d)",
            self.super_l_keycode,
            self.hyper_l_keycode,
            self._canon_super_kc,
            self._canon_hyper_kc,
        )

    def refresh_keycodes(self) -> int:
        """Refresh from the live keymap and return the physical Super keycode.

        Returns _canon_super_kc — the lower of (super_l_keycode, hyper_l_keycode)
        — which by xkb convention is the physical Super key position. This is
        stable across our own keymap swap (min/max is invariant under swapping
        the two values), so callers like the SuperPassthroughTracker can use it
        to detect *physical* Super-key presses regardless of which keysym is
        currently mapped to that position. Returning super_l_keycode directly
        would point at wherever the keysym moved to post-swap and miss the
        physical key the user actually presses.

        Returning under the same lock that wrote it keeps callers from racing
        a concurrent apply_super_hyper_swap that would mutate keycodes between
        refresh and read.
        """
        with self._lock:
            self._refresh_keycodes_locked()
            return self._canon_super_kc

    def apply_super_hyper_swap(self) -> None:
        """Swap Super_L and Hyper_L keysyms. Idempotent across self-triggered
        MappingNotify echoes — every change_keyboard_mapping call causes the X
        server to broadcast MappingNotify, which the KeyGrabber routes back to
        this method, so the idempotency check has to survive seeing the layout
        we just wrote.

        Canonical positions are re-derived from the live keymap on every call,
        so a mid-session keymap change (setxkbmap, custom Xkb layouts, etc.)
        that moves Super_L or Hyper_L to a different keycode does not leave us
        writing at stale positions and clobbering an unrelated key. The
        positional convention (lower keycode = canonical Super) keeps canon
        stable across our own swap echoes, so the idempotency check still
        reads our prior write and short-circuits.
        """
        with self._lock:
            try:
                if self.super_l_orig is None or self.hyper_l_orig is None:
                    return  # backup never completed; can't safely write
                self._refresh_keycodes_locked()
                if not self.super_l_keycode or not self.hyper_l_keycode:
                    logger.debug("Super_L or Hyper_L missing from current keymap — skipping swap.")
                    return

                # Read the keysym at the canonical Super position. Pre-swap it
                # holds Super_L; post-swap it holds Hyper_L. Using the canonical
                # (positionally stable) keycode here is what makes this
                # idempotent — a refresh-then-check approach reads back our
                # own write and concludes "not yet swapped" forever.
                current_map = self._dpy.get_keyboard_mapping(self._canon_super_kc, 1)
                already_swapped = (
                    current_map
                    and len(current_map) > 0
                    and len(current_map[0]) > 0
                    and current_map[0][0] == self.hyper_l_keysym
                )
                if already_swapped:
                    logger.debug("Super_L already mapped to Hyper_L — no action needed.")
                    return

                logger.info("Swapping Super_L and Hyper_L...")
                self._change_keyboard_mapping(self._canon_super_kc, self.hyper_l_keysym)
                self._change_keyboard_mapping(self._canon_hyper_kc, self.super_l_keysym)
                self._dpy.sync()
            except Exception as e:
                logger.error("Error rebinding keys: %s", e)
                logger.debug("", exc_info=True)

    def force_canonical(self) -> bool:
        """Restore canonical mapping (lower keycode→Super_L, higher→Hyper_L) without the swap logic.

        Returns False if either keysym is absent from the current mapping.
        Scans the live keymap directly (not via the cached keysym_to_keycode)
        so it sees mid-session keymap changes from setxkbmap and similar.
        """
        with self._lock:
            self._refresh_keycodes_locked()
            if not self.super_l_keycode or not self.hyper_l_keycode:
                return False
            self._change_keyboard_mapping(self._canon_super_kc, self.super_l_keysym)
            self._change_keyboard_mapping(self._canon_hyper_kc, self.hyper_l_keysym)
            self._dpy.sync()
            return True

    def simulate_hyper_press(self) -> None:
        """Simulate a Hyper_L key press+release (used for Super passthrough)."""
        with self._lock:
            try:
                xtest.fake_input(self._dpy, X.KeyPress, self.hyper_l_keycode)
                xtest.fake_input(self._dpy, X.KeyRelease, self.hyper_l_keycode)
                self._dpy.flush()
            except Exception as e:
                logger.error("Error simulating key: %s", e)
                logger.debug("", exc_info=True)

    def _change_keyboard_mapping(self, keycode: int, new_keysym: int) -> None:
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
        """Restore the original keysym assignments at their canonical positions."""
        logger.info("Restoring keyboard mapping...")
        with self._lock:
            try:
                if self.super_l_orig is None or self.hyper_l_orig is None:
                    logger.warning(
                        "Original key mappings unavailable — keyboard mapping not restored (backup failed at startup)."
                    )
                    return
                self._dpy.change_keyboard_mapping(self._canon_super_kc, self.super_l_orig)
                self._dpy.change_keyboard_mapping(self._canon_hyper_kc, self.hyper_l_orig)
                self._dpy.sync()
            except Exception as e:
                logger.warning("Error restoring keyboard mapping: %s", e)
                logger.debug("", exc_info=True)
