import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Seconds between wall-clock drift checks.
_WAIT_TIME = 5
# Extra seconds beyond _WAIT_TIME that indicate a sleep occurred (15s worst-case latency).
_THRESHOLD_TIME = 10


class WakeFromSleepDetector:
    def __init__(
        self,
        callback: Callable[[], None],
        wait_time: int = _WAIT_TIME,
        threshold_time: int = _THRESHOLD_TIME,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.callback = callback
        self.wait_time = wait_time
        self.threshold_time = threshold_time
        self._stop_event = stop_event or threading.Event()
        self.last_check = time.time()

    def start(self) -> None:
        """Poll for wall-clock drift until stop_event is set."""
        logger.info("Starting WakeFromSleepDetector...")
        while not self._stop_event.is_set():
            time.sleep(self.wait_time)
            now = time.time()
            if (now - self.last_check) > (self.wait_time + self.threshold_time):
                logger.info(f"System wake detected (time jump: {now - self.last_check:.2f}s)")
                self.callback()
            self.last_check = now
