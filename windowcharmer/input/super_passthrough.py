import logging
import time
from collections.abc import Callable
from typing import ClassVar

from Xlib import X
from Xlib.protocol import rq

logger = logging.getLogger(__name__)


class SuperPassthroughTracker:
    """Track Super_L press/release to open the DE menu on a bare Super tap.

    If Super is pressed and released without any other key, simulate a Hyper_L
    press (which most DEs interpret as opening the application menu). If another
    key is pressed while Super is held, suppress the menu.
    """

    # If Super is held longer than this, clear stuck state and suppress the passthrough.
    # Handles the case where a focus change consumes the KeyRelease event.
    _SUPER_TIMEOUT: ClassVar[float] = 5.0

    def __init__(self, super_keycode: int, on_passthrough: Callable[[], None]) -> None:
        self.super_keycode = super_keycode
        self.on_passthrough = on_passthrough
        self.super_pressed: bool = False
        self.key_pressed_while_super_down: bool = False
        self._super_press_time: float = 0.0

    def update_keycode(self, new_keycode: int) -> None:
        """Update the tracked keycode after a MappingNotify."""
        self.super_keycode = new_keycode

    def handle_event(self, event: rq.Event) -> None:
        """Process a KeyPress or KeyRelease event."""
        # Auto-reset stuck state: if Super has been "held" for too long, the release
        # was likely consumed by a focus change and we should clear the flag.
        if self.super_pressed and (time.time() - self._super_press_time) > self._SUPER_TIMEOUT:
            logger.debug("Super press timed out — clearing stuck state")
            self.super_pressed = False
            self.key_pressed_while_super_down = False

        if event.type == X.KeyPress:
            if event.detail == self.super_keycode:
                self.super_pressed = True
                self.key_pressed_while_super_down = False
                self._super_press_time = time.time()
                logger.debug("Super_L pressed")
            elif self.super_pressed:
                self.key_pressed_while_super_down = True

        elif event.type == X.KeyRelease and event.detail == self.super_keycode:
            self.super_pressed = False
            elapsed = time.time() - self._super_press_time
            logger.debug("Super_L released")
            if not self.key_pressed_while_super_down and elapsed < self._SUPER_TIMEOUT:
                logger.debug("Forwarding bare Super tap as Hyper_L")
                self.on_passthrough()
            self.key_pressed_while_super_down = False
