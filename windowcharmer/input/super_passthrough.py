import logging
import time
from collections.abc import Callable
from typing import ClassVar

from Xlib import X

logger = logging.getLogger(__name__)


class SuperPassthroughTracker:
    """Track Super_L press/release to open the DE menu on a bare Super tap.

    If Super is pressed and released without any other key, simulate a Hyper_L
    press (which most DEs interpret as opening the application menu). If
    another key is pressed while Super is held, suppress the menu.

    This tracker assumes upstream filtering of synthetic xtest events —
    InputManager drops events whose XI2 sourceid is in the XTEST device set,
    so events that reach handle_event are guaranteed to be from a physical
    keyboard. Without that upstream filter, simulate_super_press's own
    output would re-trigger the bare-tap detection (the synthesized event
    arrives as a press at a real keycode), looping until the daemon is
    killed.
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
        """Update the tracked keycode after a MappingNotify.

        Resets pressed state because the matching KeyRelease will now arrive
        on the new keycode — leaving super_pressed=True against the old
        keycode would make every subsequent key look like "pressed while
        Super held" until the timeout elapses.
        """
        self.super_keycode = new_keycode
        self.super_pressed = False
        self.key_pressed_while_super_down = False

    def handle_event(self, event_type: int, keycode: int) -> None:
        """Process a KeyPress or KeyRelease event."""
        if event_type == X.KeyPress:
            if keycode == self.super_keycode:
                self.super_pressed = True
                self.key_pressed_while_super_down = False
                self._super_press_time = time.monotonic()
                logger.debug("Super_L pressed")
            elif self.super_pressed:
                self.key_pressed_while_super_down = True

        elif event_type == X.KeyRelease and keycode == self.super_keycode:
            elapsed = time.monotonic() - self._super_press_time
            self.super_pressed = False
            logger.debug("Super_L released")
            # Suppress when another key was held with Super, or when the press is
            # so old that the release was likely consumed by a focus change and
            # this event is a stray.
            if not self.key_pressed_while_super_down and elapsed < self._SUPER_TIMEOUT:
                logger.debug("Forwarding bare Super tap as Hyper_L")
                self.on_passthrough()
            self.key_pressed_while_super_down = False
