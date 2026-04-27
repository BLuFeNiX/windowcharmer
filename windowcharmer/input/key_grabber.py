import contextlib
import logging
import os
import select
import time
from collections.abc import Callable
from typing import ClassVar

from Xlib import XK, X
from Xlib.display import Display
from Xlib.error import BadAccess
from Xlib.protocol import rq
from Xlib.xobject.drawable import Window

logger = logging.getLogger(__name__)

_BAD_ACCESS_RETRY_DELAY = 1.0


class KeyGrabberError(Exception):
    """Raised when the grabber loop cannot continue (e.g. BadAccess persists)."""


class KeyGrabber:
    # Modifiers to ignore when grabbing keys (NumLock, CapsLock, etc.)
    IGNORED_MODIFIERS: ClassVar[tuple[int, ...]] = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)

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
        self._stopped = False
        self._wake_r = -1
        self._wake_w = -1

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

    def stop(self) -> None:
        """Request a clean exit from start(). Safe to call from a signal handler
        or from within an event callback — sets a flag and pokes the wakeup pipe
        so a parked next_event() returns immediately.
        """
        self._stopped = True
        if self._wake_w != -1:
            with contextlib.suppress(BlockingIOError, OSError):
                os.write(self._wake_w, b"x")

    def start(self) -> None:
        """Main event loop. Retries once on BadAccess; raises KeyGrabberError on failure.

        Returns normally when stop() is called.
        """
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_w, False)
        try:
            for attempt in (1, 2):
                self.grab_keys()
                try:
                    self._run_loop()
                    return
                except BadAccess as e:
                    self.ungrab_keys()
                    if attempt < 2:
                        logger.warning(
                            f"KeyGrabber: BadAccess — another client may own a grab. "
                            f"Retrying in {_BAD_ACCESS_RETRY_DELAY:.0f}s... ({e})"
                        )
                        time.sleep(_BAD_ACCESS_RETRY_DELAY)
                        continue
                    raise KeyGrabberError("BadAccess persists after retry") from e
                except Exception as e:
                    self.ungrab_keys()
                    raise KeyGrabberError(f"event loop crashed: {e}") from e
        finally:
            os.close(self._wake_r)
            os.close(self._wake_w)
            self._wake_r = -1
            self._wake_w = -1

    def _run_loop(self) -> None:
        x_fd = self.dpy.fileno()
        while not self._stopped:
            # Drain any events the server has already sent us before parking
            # in select(). pending_events() reads buffered+socket bytes without
            # blocking, so this loop services bursts without going through
            # select on every event.
            while self.dpy.pending_events() > 0:
                self._handle_event(self.dpy.next_event())
                if self._stopped:
                    return
            # Park until either X has data or stop() pokes the wake pipe.
            ready, _, _ = select.select([x_fd, self._wake_r], [], [])
            if self._wake_r in ready:
                with contextlib.suppress(OSError):
                    os.read(self._wake_r, 4096)

    def _handle_event(self, event: rq.Event) -> None:
        if event.type == X.KeyPress:
            keycode = event.detail
            if keycode in self.keycode_map:
                self.keycode_map[keycode]()
            return

        if event.type == X.MappingNotify:
            self.dpy.refresh_keyboard_mapping(event)
            logger.debug(f"MappingNotify: request={event.request}")

            if event.request == X.MappingKeyboard:
                if self.on_mapping_notify:
                    self.on_mapping_notify(event)
                self.ungrab_keys()
                # grab_keys() re-reads current keycodes from the X server, so
                # key_combinations (keysym→callback) doesn't need to be rebuilt.
                self.grab_keys()
