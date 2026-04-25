import threading
import logging
from collections.abc import Callable
from typing import Any
from ..x11.display_pool import DisplayPool
from Xlib.display import Display
import pyudev
from .sleep_detector import WakeFromSleepDetector
from .key_monitor import KeyMonitor

logger = logging.getLogger(__name__)

class InputServices:
    """
    Manages background input monitoring services:
    - Sleep/Wake detection
    - Udev device events (keyboard plug/unplug)
    - Low-level X11 key monitoring (Passthrough logic)
    """
    def __init__(self, on_rebind_callback: Callable[[], None], on_key_event_callback: Callable[[Any], None]) -> None:
        self.on_rebind = on_rebind_callback
        self.on_key_event = on_key_event_callback
        self._threads: list[threading.Thread] = []
        self._stop_event = threading.Event()

    def start_all(self) -> None:
        """Start all monitoring threads."""
        self._start_sleep_monitor()
        self._start_udev_monitor()
        self._start_key_monitor()

    def _start_sleep_monitor(self) -> None:
        detector = WakeFromSleepDetector(callback=self.on_rebind)
        t = threading.Thread(target=detector.start, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Sleep monitor started")

    def _start_udev_monitor(self) -> None:
        def monitor_loop() -> None:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem='input')

            while not self._stop_event.is_set():
                device = monitor.poll(timeout=0.5)
                if device is None:
                    continue
                if device.action == 'add' and device.properties.get('DEVNAME', '').startswith('/dev/input/event'):
                    logger.info("Input device added, triggering rebind...")
                    self.on_rebind()

        t = threading.Thread(target=monitor_loop, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Udev monitor started")

    def _start_key_monitor(self) -> None:
        def monitor_wrapper() -> None:
            try:
                monitor_dpy: Display = DisplayPool.get_display("monitor")
                monitor = KeyMonitor(monitor_dpy, self.on_key_event)
                monitor.start()
            except OSError as e:
                logger.error(f"KeyMonitor failed to start: {e}. Super-key passthrough will not work.")

        t = threading.Thread(target=monitor_wrapper, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Key monitor started")

    def stop_all(self) -> None:
        self._stop_event.set()
        # Threads are daemon threads, so they will be killed when main process exits,
        # but we set the event to allow clean loop exit where possible.
