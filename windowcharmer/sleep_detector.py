import time
from threading import Thread

class WakeFromSleepDetector:
    def __init__(self, callback, wait_time=5, threshold_time=10):
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

    def check_sleep(self):
        """Checks if the system has been suspended since the last check."""
        now = time.time()
        if (now - self.last_check) > self.threshold_time:
            self.callback()
        self.last_check = now

    def start(self):
        """Starts monitoring for system suspend/resume cycles."""
        try:
            while True:
                time.sleep(self.wait_time)
                self.check_sleep()
        except (KeyboardInterrupt, SystemExit):
            print('Exiting wake detection loop...')

# Define a callback function
def wakeup_action():
    print("Wakeup detected!")

# Usage example:
if __name__ == "__main__":
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
