import time
from unittest.mock import MagicMock

from Xlib import X

from windowcharmer.input.super_passthrough import SuperPassthroughTracker

_SUPER = 133  # arbitrary keycode for tests


class _Ev:
    """Minimal fake X event."""

    def __init__(self, type: int, detail: int) -> None:
        self.type = type
        self.detail = detail


def _tracker() -> tuple[SuperPassthroughTracker, MagicMock]:
    cb = MagicMock()
    return SuperPassthroughTracker(super_keycode=_SUPER, on_passthrough=cb), cb


def test_bare_super_tap_fires_callback() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    cb.assert_called_once()


def test_super_plus_key_suppresses_callback() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyPress, 36))  # some other key while Super held
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    cb.assert_not_called()


def test_super_held_too_long_suppresses_callback() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    # Backdate the press so the timeout fires on the next event
    tracker._super_press_time = time.monotonic() - (SuperPassthroughTracker._SUPER_TIMEOUT + 1)
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    cb.assert_not_called()


def test_update_keycode_changes_tracked_key() -> None:
    tracker, cb = _tracker()
    tracker.update_keycode(200)
    tracker.handle_event(_Ev(X.KeyPress, 200))
    tracker.handle_event(_Ev(X.KeyRelease, 200))
    cb.assert_called_once()


def test_unrelated_key_events_ignored() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, 36))
    tracker.handle_event(_Ev(X.KeyRelease, 36))
    cb.assert_not_called()
