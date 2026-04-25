import time
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

class WakeFromSleepDetector:
    def __init__(self, callback: Callable[[], None], wait_time: int = 5, threshold_time: int = 10) -> None:
        """
        Initializes the detector.

        :param callback: Callable to be executed upon detecting a wake-up event.
        :param wait_time: Time in seconds to wait between checks.
        :param threshold_time: Time in seconds that indicates a wake-up event if exceeded between checks.
        """
        self.callback = callback
        self.wait_time = wait_time
        self.threshold_time = threshold_time
        self.last_check = time.time()

    def start(self) -> None:
        """Starts monitoring for system suspend/resume cycles."""
        logger.info("Starting WakeFromSleepDetector...")
        try:
            while True:
                time.sleep(self.wait_time)
                now = time.time()
                # If the difference between now and the last check is significantly
                # larger than the wait time, it means we probably slept.
                if (now - self.last_check) > (self.wait_time + self.threshold_time):
                    logger.info(f"System wake detected (time jump: {now - self.last_check:.2f}s)")
                    self.callback()
                self.last_check = now
        except (KeyboardInterrupt, SystemExit):
            logger.info('Exiting wake detection loop...')

