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
        """
        if not self.super_l_keycode or not self.hyper_l_keycode:
            logger.warning(
                "Super_L (kc=%d) or Hyper_L (kc=%d) not found in current keymap — "
                "keymap swap and bare-Super passthrough will be disabled.",
                self.super_l_keycode,
                self.hyper_l_keycode,
            )
            return
        try:
            self._canon_super_kc = min(self.super_l_keycode, self.hyper_l_keycode)
            self._canon_hyper_kc = max(self.super_l_keycode, self.hyper_l_keycode)
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

        Scans every shift level — modifiers like Hyper_L can be placed at a
        non-zero level by xkb layouts (Hyper_L is commonly bound only at the
        shifted level), and missing them caused the swap to bail with
        "Hyper_L missing from keymap" even when Xlib's keysym_to_keycode
        cache (which scans all levels) sees it.

        Resets keycodes to 0 first so a keysym that has been removed from the
        keymap is no longer tracked at its old position. Picks the lowest
        keycode where each keysym appears, matching Xlib's cache build order.
        """
        info = self._dpy.display.info
        kc_min, count = info.min_keycode, info.max_keycode - info.min_keycode + 1
        mapping = self._dpy.get_keyboard_mapping(kc_min, count)
        self.super_l_keycode = 0
        self.hyper_l_keycode = 0
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
        logger.debug("Refreshed keycodes: Super_L=%d, Hyper_L=%d", self.super_l_keycode, self.hyper_l_keycode)

    def refresh_keycodes(self) -> None:
        with self._lock:
            self._refresh_keycodes_locked()

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
                self._canon_super_kc = min(self.super_l_keycode, self.hyper_l_keycode)
                self._canon_hyper_kc = max(self.super_l_keycode, self.hyper_l_keycode)

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
            lo = min(self.super_l_keycode, self.hyper_l_keycode)
            hi = max(self.super_l_keycode, self.hyper_l_keycode)
            self._change_keyboard_mapping(lo, self.super_l_keysym)
            self._change_keyboard_mapping(hi, self.hyper_l_keysym)
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
        self._dpy.change_keyboard_mapping(keycode, [(new_keysym,)])

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
