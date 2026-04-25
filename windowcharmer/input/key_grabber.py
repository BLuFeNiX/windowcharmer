import logging
import sys
import time
from collections.abc import Callable
from typing import ClassVar

from Xlib import XK, X
from Xlib.display import Display
from Xlib.error import BadAccess
from Xlib.xobject.drawable import Window

logger = logging.getLogger(__name__)

_BAD_ACCESS_RETRY_DELAY = 1.0


class KeyGrabber:
    # Modifiers to ignore when grabbing keys (NumLock, CapsLock, etc.)
    IGNORED_MODIFIERS: ClassVar[list[int]] = [0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask]

    def __init__(
        self,
        dpy: Display,
        key_combinations: dict[str, Callable[[], None]],
        modifier: int = 0,
        on_mapping_notify: Callable[[object], None] | None = None,
    ) -> None:
        self.dpy = dpy
        self.key_combinations = key_combinations
        self.modifier = modifier
        self.keycode_map: dict[int, Callable[[], None]] = {}
        self.on_mapping_notify = on_mapping_notify

    def _get_keycode(self, key_name: str) -> int:
        """Return the X11 keycode for key_name, or 0 if unknown."""
        keysym = XK.string_to_keysym(key_name)
        if keysym == 0:
            return 0
        code = self.dpy.keysym_to_keycode(keysym)
        return int(code)

    def grab_keys(self) -> None:
        """Grab all configured keys on the root window."""
        root = self.dpy.screen().root
        for key_name, action in self.key_combinations.items():
            keycode = self._get_keycode(key_name)
            if keycode:
                self.keycode_map[keycode] = action
                self._grab_key_ignore_locks(root, keycode)
            else:
                logger.warning(f"Unknown key name {key_name!r} — skipping grab")

    def _grab_key_ignore_locks(self, window: Window, keycode: int) -> None:
        for mod in self.IGNORED_MODIFIERS:
            window.grab_key(keycode, self.modifier | mod, True, X.GrabModeAsync, X.GrabModeAsync)

    def ungrab_keys(self) -> None:
        """Release all grabbed keys."""
        root = self.dpy.screen().root
        for keycode in list(self.keycode_map.keys()):
            self._ungrab_key_ignore_locks(root, keycode)
        self.keycode_map.clear()

    def _ungrab_key_ignore_locks(self, window: Window, keycode: int) -> None:
        for mod in self.IGNORED_MODIFIERS:
            try:
                window.ungrab_key(keycode, self.modifier | mod)
            except Exception as e:
                logger.debug(f"Failed to ungrab keycode {keycode} mod {mod}: {e}")

    def start(self) -> None:
        """Main event loop. Retries once on BadAccess before exiting."""
        self._run_loop(retry_on_bad_access=True)

    def _run_loop(self, retry_on_bad_access: bool) -> None:
        self.grab_keys()

        try:
            while True:
                event = self.dpy.next_event()

                if event.type == X.KeyPress:
                    keycode = event.detail
                    if keycode in self.keycode_map:
                        self.keycode_map[keycode]()

                elif event.type == X.MappingNotify:
                    self.dpy.refresh_keyboard_mapping(event)
                    logger.debug(f"MappingNotify: request={event.request}")

                    if event.request == X.MappingKeyboard:
                        if self.on_mapping_notify:
                            self.on_mapping_notify(event)
                        self.ungrab_keys()
                        self.grab_keys()

        except BadAccess as e:
            self.ungrab_keys()
            if retry_on_bad_access:
                logger.warning(
                    f"KeyGrabber: BadAccess — another client may own a grab. "
                    f"Retrying in {_BAD_ACCESS_RETRY_DELAY:.0f}s... ({e})"
                )
                time.sleep(_BAD_ACCESS_RETRY_DELAY)
                self._run_loop(retry_on_bad_access=False)
            else:
                logger.error("KeyGrabber: BadAccess persists after retry — exiting.")
                sys.exit(1)
        except Exception as e:
            logger.error(f"KeyGrabber error: {e}")
            logger.debug("", exc_info=True)
            sys.exit(1)

    def stop(self) -> None:
        pass
