import logging
import threading
from collections.abc import Callable

from .sleep_detector import WakeFromSleepDetector

logger = logging.getLogger(__name__)


class InputServices:
    """Manages background monitoring threads for events the main input loop
    can't observe over the X connection.

    Currently just one: the wake-from-sleep detector, which polls wall-clock
    drift to detect a suspend/resume that paused the daemon's threads.

    Keyboard hot-plug used to live here as a pyudev-backed thread; that
    moved into InputManager via XI2 HierarchyChanged events on the main
    event loop. Same for the XRecord-based KeyMonitor — XI2 events arrive
    on the main connection now.
    """

    def __init__(self, on_rebind_callback: Callable[[], None]) -> None:
        self.on_rebind = on_rebind_callback
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()

    def start_all(self) -> None:
        self._start_sleep_monitor()

    def _start_sleep_monitor(self) -> None:
        detector = WakeFromSleepDetector(callback=self.on_rebind, stop_event=self._stop_event)
        t = threading.Thread(target=detector.start, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Sleep monitor started")

    def stop_all(self) -> None:
        """Signal all monitors to stop and wait for them to exit."""
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=2.0)
            if t.is_alive():
                logger.warning("Input monitor thread %s did not exit within timeout", t.name)
