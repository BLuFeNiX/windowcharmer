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

    def start(self) -> None:
        """Poll for wall-clock drift until stop_event is set."""
        logger.info("Starting WakeFromSleepDetector...")
        # Anchor the baseline at the moment polling actually begins, not at
        # __init__: otherwise a long delay between construction and start()
        # gets misread as a sleep on the first tick.
        last_check = time.time()
        # wait() returns True when the event fires, False on timeout — so the
        # loop only continues after a full wait_time has elapsed and shuts
        # down promptly when stop_event is set.
        while not self._stop_event.wait(self.wait_time):
            now = time.time()
            if (now - last_check) > (self.wait_time + self.threshold_time):
                logger.info("System wake detected (time jump: %.2fs)", now - last_check)
                self.callback()
            last_check = now
