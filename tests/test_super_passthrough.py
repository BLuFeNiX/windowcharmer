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


def test_update_keycode_clears_in_flight_press_state() -> None:
    """Regression: if Super is held when the keymap rebinds, the matching
    KeyRelease arrives on the new keycode and the old super_pressed=True
    would mis-classify any subsequent key as 'pressed while Super held',
    suppressing the menu indefinitely.
    """
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    assert tracker.super_pressed is True

    # Keymap rebinds while Super is still physically held.
    tracker.update_keycode(200)
    assert tracker.super_pressed is False
    assert tracker.key_pressed_while_super_down is False

    # An unrelated key press now must not poison state for the next tap.
    tracker.handle_event(_Ev(X.KeyPress, 36))
    tracker.handle_event(_Ev(X.KeyPress, 200))
    tracker.handle_event(_Ev(X.KeyRelease, 200))
    cb.assert_called_once()


def test_unrelated_key_events_ignored() -> None:
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, 36))
    tracker.handle_event(_Ev(X.KeyRelease, 36))
    cb.assert_not_called()


def test_synthetic_events_after_passthrough_do_not_re_trigger() -> None:
    """Regression: post-swap, simulate_hyper_press fakes input at the keycode
    where Hyper_L now lives — which equals canon_super_kc on the typical xkb
    US layout, the SAME keycode this tracker watches. Without suppression,
    each forwarded tap would re-trigger the tracker and loop forever.
    """
    tracker, cb = _tracker()
    # Real bare tap fires the callback once.
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    cb.assert_called_once()

    # Simulate the synthetic events that on_passthrough's xtest.fake_input
    # produces — they come back through XRecord as press+release at _SUPER.
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    # No second invocation: the synthetic pair was suppressed.
    cb.assert_called_once()


def test_real_super_press_after_synthetic_pair_works() -> None:
    """Once the synthetic press+release has been consumed, a subsequent real
    tap must register normally — the suppression must be exactly two events,
    not "until reset."
    """
    tracker, cb = _tracker()
    # First real tap → fires + arms suppression.
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    # Synthetic round-trip consumes the two pending.
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    assert cb.call_count == 1

    # Second real tap should fire again.
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    assert cb.call_count == 2


def test_update_keycode_clears_pending_synthetic_count() -> None:
    """If a MappingNotify lands between firing and receiving the synthetic
    pair (e.g. a layout change races the simulate flush), the pending count
    belongs to the OLD keycode. Leaving it set would over-suppress real
    events at the new one.
    """
    tracker, cb = _tracker()
    tracker.handle_event(_Ev(X.KeyPress, _SUPER))
    tracker.handle_event(_Ev(X.KeyRelease, _SUPER))
    # Pending is now 2 (waiting for synthetic round-trip).
    assert tracker._pending_synthetic == 2

    # Keymap changes; tracker re-points to a new keycode.
    tracker.update_keycode(200)
    assert tracker._pending_synthetic == 0

    # Real tap on the new keycode fires immediately — no leftover suppression.
    tracker.handle_event(_Ev(X.KeyPress, 200))
    tracker.handle_event(_Ev(X.KeyRelease, 200))
    assert cb.call_count == 2
