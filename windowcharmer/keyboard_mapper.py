import threading
import logging
from Xlib import X, XK, display
from Xlib.ext import xtest
import traceback

logger = logging.getLogger(__name__)

class KeyboardMapper:
    """
    Handles the complex logic of remapping Super_L <-> Hyper_L and simulating key events.
    This encapsulates the state and locking required to make X11 keyboard modifications safe.
    """
    def __init__(self):
        self._lock = threading.Lock()
        # We need a dedicated display connection for mapping operations
        self._dpy = display.Display()
        
        # Calculate keycodes/keysyms once
        self.super_l_keysym = XK.string_to_keysym('Super_L')
        self.hyper_l_keysym = XK.string_to_keysym('Hyper_L')
        self.super_l_keycode = self._dpy.keysym_to_keycode(self.super_l_keysym)
        self.hyper_l_keycode = self._dpy.keysym_to_keycode(self.hyper_l_keysym)

        # Backup original mappings (for cleanup, though we don't strictly use them yet)
        self.super_l_orig = self._dpy.get_keyboard_mapping(self.super_l_keycode, 1)
        try:
            self.hyper_l_orig = self._dpy.get_keyboard_mapping(self.hyper_l_keycode, 1)
        except Exception:
            logger.error("Error: No mapping for Hyper_L found during init.")
            # We continue, but this might be fatal depending on setup

    def refresh_keycodes(self):
        """Re-fetch keycodes from X server if mapping changed externally."""
        self.super_l_keycode = self._dpy.keysym_to_keycode(self.super_l_keysym)
        self.hyper_l_keycode = self._dpy.keysym_to_keycode(self.hyper_l_keysym)
        logger.debug(f"Refreshed keycodes: Super_L={self.super_l_keycode}, Hyper_L={self.hyper_l_keycode}")

    def apply_super_hyper_swap(self):
        """
        Swaps the Super_L and Hyper_L keysyms on the keyboard mapping.
        Safe to call repeatedly; checks current state first.
        """
        with self._lock:
            try:
                # Check if swap is needed to prevent loops and redundant calls
                current_map = self._dpy.get_keyboard_mapping(self.super_l_keycode, 1)
                if current_map and len(current_map) > 0 and len(current_map[0]) > 0:
                    if current_map[0][0] == self.hyper_l_keysym:
                        logger.debug("Super_L is already mapped to Hyper_L. No action needed.")
                        return

                logger.info("Swapping Super_L and Hyper_L...")
                self._change_keyboard_mapping(self.super_l_keycode, self.hyper_l_keysym)
                self._change_keyboard_mapping(self.hyper_l_keycode, self.super_l_keysym)
                self._dpy.sync()
            except Exception as e:
                logger.error(f"Error rebinding keys: {e}")
                logger.debug(traceback.format_exc())

    def simulate_hyper_press(self):
        """Simulate a press and release of the Hyper_L key."""
        with self._lock:
            try:
                # We simulate Hyper_L keycode, which we mapped to Super_L keysym
                xtest.fake_input(self._dpy, X.KeyPress, self.hyper_l_keycode)
                xtest.fake_input(self._dpy, X.KeyRelease, self.hyper_l_keycode)
                self._dpy.flush()
            except Exception as e:
                logger.error(f"Error simulating key: {e}")

    def _change_keyboard_mapping(self, keycode, new_keysym):
        keysyms = [(new_keysym,)]
        self._dpy.change_keyboard_mapping(keycode, keysyms)
        self._dpy.flush()

    def cleanup(self):
        """Restore original mappings."""
        logger.info("Restoring keyboard mapping...")
        try:
            # Use a fresh connection or the existing one? Existing is fine if we lock or are shutting down.
            # But main.py used a fresh one. Let's try existing first.
            self._dpy.change_keyboard_mapping(self.super_l_keycode, self.super_l_orig)
            self._dpy.change_keyboard_mapping(self.hyper_l_keycode, self.hyper_l_orig)
            self._dpy.sync()
            self._dpy.close()
        except Exception:
            pass
