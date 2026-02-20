import threading
from Xlib import X, XK, display
import traceback
import logging

logger = logging.getLogger(__name__)

class KeyGrabber:
    def __init__(self, dpy, key_combinations, modifier=0):
        self.dpy = dpy
        self.key_combinations = key_combinations
        self.modifier = modifier
        self.keycode_map = {}

    def _get_keycode(self, key_name):
        keysym = XK.string_to_keysym(key_name)
        if keysym == 0:
            return 0
        return self.dpy.keysym_to_keycode(keysym)

    def grab_keys(self):
        root = self.dpy.screen().root
        # Grab all desired keys
        for key_name, action in self.key_combinations.items():
            keycode = self._get_keycode(key_name)
            if keycode:
                self.keycode_map[keycode] = action
                self._grab_key_ignore_locks(root, keycode)

    def _grab_key_ignore_locks(self, window, keycode):
        # We need to grab the key with various lock modifiers (NumLock, CapsLock, etc.)
        # Common modifiers to ignore:
        # Mod2 (NumLock), Lock (CapsLock), Mod5 (ScrollLock - sometimes), etc.
        # Minimal set: None, Lock, Mod2, Lock|Mod2
        modifiers = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]
        
        for mod in modifiers:
            window.grab_key(keycode, self.modifier | mod, True, X.GrabModeAsync, X.GrabModeAsync)

    def start(self):
        self.grab_keys()
        try:
            while True:
                event = self.dpy.next_event()
                if event.type == X.KeyPress:
                    keycode = event.detail
                    if keycode in self.keycode_map:
                        callback = self.keycode_map[keycode]
                        # Fire and forget action in a separate thread if needed,
                        # or run quickly here. The original code ran actions directly.
                        # Actions are usually quick X requests.
                        callback()
        except Exception as e:
            logger.error(f"KeyGrabber error: {e}")
            logger.debug(traceback.format_exc())

    def stop(self):
        # Clean up grabs if necessary
        # Usually handled by closing the display connection or exiting process
        pass
