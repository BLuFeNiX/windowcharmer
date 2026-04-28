"""Tests for RebindScheduler — the keymap-rebind debouncer.

The class collapses bursts of rebind triggers (sleep wake-ups, keyboard
hot-plugs) into a single delayed callback. shutdown() must drain any
in-flight timer so a late fire can't re-swap the keymap after cleanup.
"""

import threading
from unittest.mock import MagicMock, patch

from windowcharmer.input.rebind_scheduler import RebindScheduler


def test_schedule_arms_a_timer() -> None:
    cb = MagicMock()
    fake_timer = MagicMock()
    with patch("windowcharmer.input.rebind_scheduler.threading.Timer", return_value=fake_timer) as TimerCls:
        sched = RebindScheduler(callback=cb, delay_seconds=0.25)
        sched.schedule()

    TimerCls.assert_called_once_with(0.25, cb)
    fake_timer.start.assert_called_once()


def test_back_to_back_schedule_cancels_the_first_timer() -> None:
    """Two rebinds in quick succession collapse to a single fire."""
    cb = MagicMock()
    timer1 = MagicMock()
    timer2 = MagicMock()
    with patch("windowcharmer.input.rebind_scheduler.threading.Timer", side_effect=[timer1, timer2]):
        sched = RebindScheduler(callback=cb, delay_seconds=0.25)
        sched.schedule()
        sched.schedule()

    timer1.cancel.assert_called_once()
    timer1.start.assert_called_once()
    timer2.cancel.assert_not_called()
    timer2.start.assert_called_once()


def test_shutdown_cancels_and_joins_pending_timer() -> None:
    """shutdown() must wait for an in-flight callback so a late fire can't
    re-swap after the daemon's restoration ran."""
    cb = MagicMock()
    fake_timer = MagicMock()
    with patch("windowcharmer.input.rebind_scheduler.threading.Timer", return_value=fake_timer):
        sched = RebindScheduler(callback=cb, delay_seconds=0.25)
        sched.schedule()
        sched.shutdown()

    fake_timer.cancel.assert_called_once()
    fake_timer.join.assert_called_once()


def test_shutdown_with_no_pending_timer_is_noop() -> None:
    cb = MagicMock()
    sched = RebindScheduler(callback=cb, delay_seconds=0.25)
    # Should not raise.
    sched.shutdown()
    cb.assert_not_called()


def test_callback_actually_fires_after_delay() -> None:
    """End-to-end: the real Timer fires the callback. Uses a tiny delay so
    the test still runs fast — guards against accidental timer-arming bugs
    that pure mocking would mask."""
    fired = threading.Event()
    sched = RebindScheduler(callback=fired.set, delay_seconds=0.01)
    sched.schedule()
    assert fired.wait(timeout=1.0), "callback did not fire within timeout"
    sched.shutdown()
