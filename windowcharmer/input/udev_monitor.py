import logging
import threading
from collections.abc import Callable

import pyudev

logger = logging.getLogger(__name__)


class UdevKeyboardMonitor:
    """Watch /dev/input/event* for keyboard hotplug; fire callback on add."""

    def __init__(self, callback: Callable[[], None], stop_event: threading.Event) -> None:
        self._callback = callback
        self._stop_event = stop_event

    def start(self) -> None:
        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="input")

            while not self._stop_event.is_set():
                device = monitor.poll(timeout=0.5)
                if device is None:
                    continue
                if (
                    device.action == "add"
                    and device.properties.get("DEVNAME", "").startswith("/dev/input/event")
                    and device.properties.get("ID_INPUT_KEYBOARD") == "1"
                ):
                    logger.info("Keyboard added, triggering rebind...")
                    self._callback()
        except Exception as e:
            logger.error(f"Udev monitor failed: {e}")
            logger.debug("", exc_info=True)
