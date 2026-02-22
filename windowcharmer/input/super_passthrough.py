from __future__ import annotations
import logging
from typing import Any
from Xlib import X

logger = logging.getLogger(__name__)

class SuperPassthroughTracker:
    """
    Tracks the state of the Super key (Super_L) to handle passthrough logic.
    If Super is pressed and released without any other key being pressed,
    it simulates a Hyper_L press (which the DE usually interprets as opening the menu).
    If another key is pressed while Super is down, it suppresses the menu opening.
    """
    def __init__(self, super_keycode: int, on_passthrough: Any) -> None:
        self.super_keycode = super_keycode
        self.on_passthrough = on_passthrough
        self.super_pressed: bool = False
        self.key_pressed_while_super_down: bool = False

    def update_keycode(self, new_keycode: int) -> None:
        self.super_keycode = new_keycode

    def handle_event(self, event: Any) -> None:
        """
        Process a KeyPress or KeyRelease event.
        """
        if event.type == X.KeyPress:
            if event.detail == self.super_keycode:
                self.super_pressed = True
                self.key_pressed_while_super_down = False
                logger.debug("Super_L key pressed")
            elif self.super_pressed:
                self.key_pressed_while_super_down = True
        
        elif event.type == X.KeyRelease:
            if event.detail == self.super_keycode:
                self.super_pressed = False
                logger.debug("Super_L key released")
                if not self.key_pressed_while_super_down:
                    logger.debug("Forwarding super press as Hyper_L")
                    self.on_passthrough()
                self.key_pressed_while_super_down = False
