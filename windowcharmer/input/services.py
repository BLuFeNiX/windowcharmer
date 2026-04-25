import logging
import threading
from collections.abc import Callable
from typing import Any

import pyudev
from Xlib.display import Display

from ..x11.display_pool import DisplayPool
from .key_monitor import KeyMonitor
from .sleep_detector import WakeFromSleepDetector

logger = logging.getLogger(__name__)


class InputServices:
    """Manages background input monitoring: sleep/wake, keyboard hotplug, XRecord key events."""

    def __init__(self, on_rebind_callback: Callable[[], None], on_key_event_callback: Callable[[Any], None]) -> None:
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
        def monitor_loop() -> None:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem='input')

            while not self._stop_event.is_set():
                device = monitor.poll(timeout=0.5)
                if device is None:
                    continue
                if (
                    device.action == 'add'
                    and device.properties.get('DEVNAME', '').startswith('/dev/input/event')
                    and device.properties.get('ID_INPUT_KEYBOARD') == '1'
                ):
                    logger.info("Keyboard added, triggering rebind...")
                    self.on_rebind()

        t = threading.Thread(target=monitor_loop, daemon=True)
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
                logger.error(f"KeyMonitor failed to start: {e}. Super-key passthrough will not work.")

        t = threading.Thread(target=monitor_wrapper, daemon=True)
        t.start()
        self._threads.append(t)
        logger.debug("Key monitor started")

    def stop_all(self) -> None:
        """Signal all monitors to stop and clean up."""
        self._stop_event.set()
        if self._key_monitor:
            self._key_monitor.stop()
