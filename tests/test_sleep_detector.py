"""Unit tests for WakeFromSleepDetector."""

import threading
import time
from unittest.mock import MagicMock

import pytest

from windowcharmer.input.sleep_detector import WakeFromSleepDetector


def test_stop_event_exits_loop_promptly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting stop_event must wake the wait() and end the loop within ~one tick.

    The loop sleeps via stop_event.wait(_WAIT_TIME); the bug this guards
    against was an earlier time.sleep-based loop that blocked shutdown
    for the full sleep duration.
    """
    monkeypatch.setattr("windowcharmer.input.sleep_detector._WAIT_TIME", 10)
    stop = threading.Event()
    detector = WakeFromSleepDetector(callback=MagicMock(), stop_event=stop)
    thread = threading.Thread(target=detector.start)
    thread.start()

    # Give the loop a moment to enter wait()
    time.sleep(0.05)
    stop.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "detector thread did not exit promptly after stop_event"
