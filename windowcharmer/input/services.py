import logging
import threading
from collections.abc import Callable

from Xlib.display import Display
from Xlib.protocol import rq

from ..x11.display_pool import DisplayPool
from .key_monitor import KeyMonitor
from .sleep_detector import WakeFromSleepDetector
from .udev_monitor import UdevKeyboardMonitor

logger = logging.getLogger(__name__)


class InputServices:
    """Manages background input monitoring: sleep/wake, keyboard hotplug, XRecord key events."""

    def __init__(
        self,
        on_rebind_callback: Callable[[], None],
        on_key_event_callback: Callable[[rq.Event], None],
    ) -> None:
        self.on_rebind = on_rebind_callback
        self.on_key_event = on_key_event_callback
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()
        self._key_monitor: KeyMonitor | None = None

    def start_all(self) -> None:
        """Start all monitoring threads."""
        self._start_sleep_monitor()
        self._start_udev_monitor()
        self._start_key_monitor()

    def _start_sleep_monitor(self) -> None:
        detector = WakeFromSleepDetector(callback=self.on_rebind, stop_event=self._stop_event)
        t = threading.Thread(target=detector.start, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Sleep monitor started")

    def _start_udev_monitor(self) -> None:
        monitor = UdevKeyboardMonitor(callback=self.on_rebind, stop_event=self._stop_event)
        t = threading.Thread(target=monitor.start, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Udev monitor started")

    def _start_key_monitor(self) -> None:
        monitor_dpy: Display = DisplayPool.get_display("monitor")
        self._key_monitor = KeyMonitor(monitor_dpy, self.on_key_event)
        monitor = self._key_monitor

        def monitor_wrapper() -> None:
            try:
                monitor.start()
            except OSError as e:
                logger.error("KeyMonitor failed to start: %s. Super-key passthrough will not work.", e)

        t = threading.Thread(target=monitor_wrapper, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Key monitor started")

    def stop_all(self) -> None:
        """Signal all monitors to stop and wait for them to exit.

        Joining ensures monitor callbacks (which may touch the mapper or
        other shared resources) don't fire after the caller proceeds to
        tear those resources down.
        """
        self._stop_event.set()
        if self._key_monitor:
            self._key_monitor.stop()
        for t in self._threads:
            t.join(timeout=2.0)
            if t.is_alive():
                logger.warning("Input monitor thread %s did not exit within timeout", t.name)
