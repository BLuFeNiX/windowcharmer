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

    --- FEEDBACK-LOOP HAZARD ---
    on_passthrough is wired to KeyboardMapper.simulate_hyper_press, which uses
    xtest.fake_input to inject a Hyper_L press+release. Post-keymap-swap on the
    typical xkb US layout, Hyper_L lives at canon_super_kc (the lower of the
    two canonical keycodes) — which is the SAME keycode this tracker watches
    for physical Super presses. Synthetic events from xtest are wire-
    indistinguishable from real input and are delivered back to us via
    XRecord, so without explicit suppression the synthetic press would re-
    trigger the bare-tap detector and call on_passthrough again, looping
    until the daemon is killed.

    `_pending_synthetic` is a count of events at super_keycode the tracker
    expects to receive (and drop) following a self-fired passthrough. It's
    pre-armed BEFORE invoking on_passthrough so the synthetic events can't
    arrive before the guard is set.
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
        # See class docstring's FEEDBACK-LOOP HAZARD section.
        self._pending_synthetic: int = 0

    def update_keycode(self, new_keycode: int) -> None:
        """Update the tracked keycode after a MappingNotify.

        Resets pressed state because the matching KeyRelease will now arrive
        on the new keycode — leaving super_pressed=True against the old
        keycode would make every subsequent key look like "pressed while
        Super held" until the timeout elapses. Also resets the synthetic
        suppression count: any pending count belongs to the previous keycode
        and would over-suppress real events at the new one.
        """
        self.super_keycode = new_keycode
        self.super_pressed = False
        self.key_pressed_while_super_down = False
        self._pending_synthetic = 0

    def handle_event(self, event: rq.Event) -> None:
        """Process a KeyPress or KeyRelease event."""
        # Drop synthetic events from our own simulate_hyper_press to avoid the
        # feedback loop described in the class docstring. Press and release
        # arrive in order via XRecord, so a count-of-2 (set by the bare-tap
        # branch below before invoking on_passthrough) consumes exactly one
        # synthetic press+release pair. update_keycode() resets the count if
        # something pathological eats one of the events, so we don't get
        # stuck dropping forever.
        if (
            event.type in (X.KeyPress, X.KeyRelease)
            and event.detail == self.super_keycode
            and self._pending_synthetic > 0
        ):
            self._pending_synthetic -= 1
            return

        if event.type == X.KeyPress:
            if event.detail == self.super_keycode:
                self.super_pressed = True
                self.key_pressed_while_super_down = False
                self._super_press_time = time.monotonic()
                logger.debug("Super_L pressed")
            elif self.super_pressed:
                self.key_pressed_while_super_down = True

        elif event.type == X.KeyRelease and event.detail == self.super_keycode:
            elapsed = time.monotonic() - self._super_press_time
            self.super_pressed = False
            logger.debug("Super_L released")
            # Suppress when another key was held with Super, or when the press is
            # so old that the release was likely consumed by a focus change and
            # this event is a stray.
            if not self.key_pressed_while_super_down and elapsed < self._SUPER_TIMEOUT:
                logger.debug("Forwarding bare Super tap as Hyper_L")
                # Pre-arm the suppression count BEFORE firing the callback —
                # simulate_hyper_press flushes synchronously, so the synthetic
                # events can begin arriving on the XRecord channel before the
                # function returns. See class docstring.
                self._pending_synthetic = 2  # one synthetic press + one release
                self.on_passthrough()
            self.key_pressed_while_super_down = False
