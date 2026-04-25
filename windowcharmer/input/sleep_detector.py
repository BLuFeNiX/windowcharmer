import time
from threading import Thread
import logging
from typing import Callable

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
                    if self.callback is not None:
                        self.callback()
                self.last_check = now
        except (KeyboardInterrupt, SystemExit):
            logger.info('Exiting wake detection loop...')

# Define a callback function
def wakeup_action() -> None:
    logger.info("Wakeup detected!")

# Usage example:
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    detector = WakeFromSleepDetector(callback=wakeup_action)
    t = Thread(target=detector.start)
    t.daemon = True
    t.start()

    # The main thread can perform other tasks here,
    # or simply wait for the monitoring thread to be interrupted.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
