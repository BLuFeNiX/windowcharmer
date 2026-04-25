import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


class WakeFromSleepDetector:
    def __init__(
        self,
        callback: Callable[[], None],
        wait_time: int = 5,
        threshold_time: int = 10,
        stop_event: threading.Event | None = None,
    ) -> None:
        """
        :param callback: Called when a suspend→resume transition is detected.
        :param wait_time: Seconds between wall-clock drift checks.
        :param threshold_time: Extra seconds beyond wait_time that indicate a sleep occurred.
        :param stop_event: Set this event to shut down the detector cleanly.
        """
        self.callback = callback
        self.wait_time = wait_time
        self.threshold_time = threshold_time
        self._stop_event = stop_event or threading.Event()
        self.last_check = time.time()

    def start(self) -> None:
        """Poll for wall-clock drift until stop_event is set."""
        logger.info("Starting WakeFromSleepDetector...")
        try:
            while not self._stop_event.is_set():
                time.sleep(self.wait_time)
                now = time.time()
                if (now - self.last_check) > (self.wait_time + self.threshold_time):
                    logger.info(f"System wake detected (time jump: {now - self.last_check:.2f}s)")
                    self.callback()
                self.last_check = now
        except (KeyboardInterrupt, SystemExit):
            logger.info("Exiting wake detection loop...")
