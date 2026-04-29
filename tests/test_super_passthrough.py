import time
from unittest.mock import MagicMock

from Xlib import X

from windowcharmer.input.super_passthrough import SuperPassthroughTracker

_SUPER = 133  # arbitrary keycode for tests


def _tracker() -> tuple[SuperPassthroughTracker, MagicMock]:
    cb = MagicMock()
    return SuperPassthroughTracker(super_keycode=_SUPER, on_passthrough=cb), cb


def test_bare_super_tap_fires_callback() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(X.KeyPress, _SUPER)
    tracker.handle_event(X.KeyRelease, _SUPER)
    cb.assert_called_once()


def test_super_plus_key_suppresses_callback() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(X.KeyPress, _SUPER)
    tracker.handle_event(X.KeyPress, 36)  # some other key while Super held
    tracker.handle_event(X.KeyRelease, _SUPER)
    cb.assert_not_called()


def test_super_held_too_long_suppresses_callback() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(X.KeyPress, _SUPER)
    # Backdate the press so the timeout fires on the next event
    tracker._super_press_time = time.monotonic() - (SuperPassthroughTracker._SUPER_TIMEOUT + 1)
    tracker.handle_event(X.KeyRelease, _SUPER)
    cb.assert_not_called()


def test_update_keycode_changes_tracked_key() -> None:
    tracker, cb = _tracker()
    tracker.update_keycode(200)
    tracker.handle_event(X.KeyPress, 200)
    tracker.handle_event(X.KeyRelease, 200)
    cb.assert_called_once()


def test_update_keycode_clears_in_flight_press_state() -> None:
    """Regression: if Super is held when the keymap rebinds, the matching
    KeyRelease arrives on the new keycode and the old super_pressed=True
    would mis-classify any subsequent key as 'pressed while Super held',
    suppressing the menu indefinitely.
    """
    tracker, cb = _tracker()
    tracker.handle_event(X.KeyPress, _SUPER)
    assert tracker.super_pressed is True

    # Keymap rebinds while Super is still physically held.
    tracker.update_keycode(200)
    assert tracker.super_pressed is False
    assert tracker.key_pressed_while_super_down is False

    # An unrelated key press now must not poison state for the next tap.
    tracker.handle_event(X.KeyPress, 36)
    tracker.handle_event(X.KeyPress, 200)
    tracker.handle_event(X.KeyRelease, 200)
    cb.assert_called_once()


def test_unrelated_key_events_ignored() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(X.KeyPress, 36)
    tracker.handle_event(X.KeyRelease, 36)
    cb.assert_not_called()


def test_on_release_fires_for_every_super_release() -> None:
    """The Super+Tab cycle uses ``on_release`` to end its session
    so subsequent presses start fresh. It must fire on EVERY Super
    release — bare-tap and held-with-key — not just the bare-tap
    case that triggers the menu passthrough."""
    cb = MagicMock()
    on_release = MagicMock()
    tracker = SuperPassthroughTracker(_SUPER, cb, on_release=on_release)

    # Held with key — bare-tap suppressed, on_release must still fire.
    tracker.handle_event(X.KeyPress, _SUPER)
    tracker.handle_event(X.KeyPress, 36)
    tracker.handle_event(X.KeyRelease, _SUPER)
    cb.assert_not_called()
    assert on_release.call_count == 1

    # Bare tap — both fire.
    tracker.handle_event(X.KeyPress, _SUPER)
    tracker.handle_event(X.KeyRelease, _SUPER)
    assert cb.call_count == 1
    assert on_release.call_count == 2


def test_on_release_optional() -> None:
    """Tracker works without an on_release callback (legacy / minimal callers)."""
    tracker = SuperPassthroughTracker(_SUPER, MagicMock())  # no on_release
    tracker.handle_event(X.KeyPress, _SUPER)
    tracker.handle_event(X.KeyRelease, _SUPER)  # must not raise
