"""Unit tests for WakeFromSleepDetector."""

import threading
import time
from unittest.mock import MagicMock

from windowcharmer.input.sleep_detector import WakeFromSleepDetector


def test_stop_event_exits_loop_promptly() -> None:
    """Setting stop_event must wake the wait() and end the loop within ~one tick.

    The loop sleeps via stop_event.wait(wait_time); a long wait_time previously
    blocked shutdown for the full sleep duration when time.sleep was used.
    """
    stop = threading.Event()
    detector = WakeFromSleepDetector(callback=MagicMock(), wait_time=10, stop_event=stop)
    thread = threading.Thread(target=detector.start)
    thread.start()

    # Give the loop a moment to enter wait()
    time.sleep(0.05)
    stop.set()
    thread.join(timeout=1.0)
    assert not thread.is_alive(), "detector thread did not exit promptly after stop_event"
