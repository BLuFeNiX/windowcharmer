import threading
from Xlib import X, XK, display
import traceback
import logging

logger = logging.getLogger(__name__)

class KeyGrabber:
    # Modifiers to ignore when grabbing keys (NumLock, CapsLock, etc.)
    # Minimal set: None, Lock, Mod2, Lock|Mod2
    IGNORED_MODIFIERS = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]

    def __init__(self, dpy, key_combinations, modifier=0, on_mapping_notify=None):
        self.dpy = dpy
        self.key_combinations = key_combinations
        self.modifier = modifier
        self.keycode_map = {}
        self.on_mapping_notify = on_mapping_notify

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
        for mod in self.IGNORED_MODIFIERS:
            window.grab_key(keycode, self.modifier | mod, True, X.GrabModeAsync, X.GrabModeAsync)

    def ungrab_keys(self):
        root = self.dpy.screen().root
        for keycode in list(self.keycode_map.keys()):
            self._ungrab_key_ignore_locks(root, keycode)
        self.keycode_map.clear()

    def _ungrab_key_ignore_locks(self, window, keycode):
        for mod in self.IGNORED_MODIFIERS:
            try:
                window.ungrab_key(keycode, self.modifier | mod)
            except Exception:
                pass

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
                elif event.type == X.MappingNotify:
                    # Update Xlib's internal mapping
                    self.dpy.refresh_keyboard_mapping(event)
                    logger.debug(f"MappingNotify received: request={event.request}")

                    if event.request == X.MappingKeyboard:
                        # Notify external listener (rebind_super) FIRST to fix the mapping
                        if self.on_mapping_notify:
                            self.on_mapping_notify()
                        
                        # THEN re-grab keys with the (potentially) updated mapping
                        self.ungrab_keys()
                        self.grab_keys()
        except Exception as e:
            logger.error(f"KeyGrabber error: {e}")
            logger.debug(traceback.format_exc())

    def stop(self):
        # Clean up grabs if necessary
        # Usually handled by closing the display connection or exiting process
        pass
