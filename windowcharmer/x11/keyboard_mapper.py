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

        self.super_l_orig: list[list[int]] | None = None
        self.hyper_l_orig: list[list[int]] | None = None

        self._backup_mappings()

    def _backup_mappings(self) -> None:
        """Snapshot the current keysym assignments so cleanup() can restore them."""
        try:
            super_map = self._dpy.get_keyboard_mapping(self.super_l_keycode, 1)
            hyper_map = self._dpy.get_keyboard_mapping(self.hyper_l_keycode, 1)
            # If a previous run crashed mid-swap, the mapping is already inverted.
            # Restore canonical keysyms so cleanup() doesn't restore the swapped state.
            if super_map and len(super_map) > 0 and len(super_map[0]) > 0 and super_map[0][0] == self.hyper_l_keysym:
                self.super_l_orig = [[self.super_l_keysym]]
                self.hyper_l_orig = [[self.hyper_l_keysym]]
            else:
                self.super_l_orig = super_map
                self.hyper_l_orig = hyper_map
        except Exception as e:
            logger.error(f"Error backing up key mappings: {e}")
            logger.debug("", exc_info=True)

    def refresh_keycodes(self) -> None:
        """Re-fetch keycodes from the X server after a MappingNotify."""
        with self._lock:
            self.super_l_keycode = self._dpy.keysym_to_keycode(self.super_l_keysym)
            self.hyper_l_keycode = self._dpy.keysym_to_keycode(self.hyper_l_keysym)
            logger.debug(f"Refreshed keycodes: Super_L={self.super_l_keycode}, Hyper_L={self.hyper_l_keycode}")

    def apply_super_hyper_swap(self) -> None:
        """Swap Super_L and Hyper_L keysyms. Idempotent — checks current state first."""
        with self._lock:
            try:
                current_map = self._dpy.get_keyboard_mapping(self.super_l_keycode, 1)
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
                self._change_keyboard_mapping(self.super_l_keycode, self.hyper_l_keysym)
                self._change_keyboard_mapping(self.hyper_l_keycode, self.super_l_keysym)
                self._dpy.sync()
            except Exception as e:
                logger.error(f"Error rebinding keys: {e}")
                logger.debug("", exc_info=True)

    def force_canonical(self) -> bool:
        """Restore canonical mapping (lower keycode→Super_L, higher→Hyper_L) without the swap logic.

        Returns False if either keysym is absent from the current mapping.
        """
        with self._lock:
            kc_a = self._dpy.keysym_to_keycode(self.super_l_keysym)
            kc_b = self._dpy.keysym_to_keycode(self.hyper_l_keysym)
            if not kc_a or not kc_b:
                return False
            lo, hi = min(kc_a, kc_b), max(kc_a, kc_b)
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
                logger.error(f"Error simulating key: {e}")
                logger.debug("", exc_info=True)

    def _change_keyboard_mapping(self, keycode: int, new_keysym: int) -> None:
        self._dpy.change_keyboard_mapping(keycode, [(new_keysym,)])
        self._dpy.flush()

    def cleanup(self) -> None:
        """Restore the original keysym assignments."""
        logger.info("Restoring keyboard mapping...")
        try:
            if self.super_l_orig is None or self.hyper_l_orig is None:
                logger.warning(
                    "Original key mappings unavailable — keyboard mapping not restored (backup failed at startup)."
                )
                return
            self._dpy.change_keyboard_mapping(self.super_l_keycode, self.super_l_orig)
            self._dpy.change_keyboard_mapping(self.hyper_l_keycode, self.hyper_l_orig)
            self._dpy.sync()
        except Exception as e:
            logger.warning(f"Error restoring keyboard mapping: {e}")
            logger.debug("", exc_info=True)
