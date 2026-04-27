"""Unit tests for UdevKeyboardMonitor."""

import threading
from unittest.mock import MagicMock, patch

from windowcharmer.input.udev_monitor import UdevKeyboardMonitor


def _device(action: str = "add", devname: str = "/dev/input/event5", is_keyboard: str = "1") -> MagicMock:
    """Stub a pyudev Device matching the monitor's filter contract."""
    dev = MagicMock()
    dev.action = action
    # The monitor only calls device.properties.get(...), so a real dict is enough.
    dev.properties = {"DEVNAME": devname, "ID_INPUT_KEYBOARD": is_keyboard}
    return dev


def _run_monitor(devices: list[object]) -> tuple[MagicMock, threading.Event]:
    """Run UdevKeyboardMonitor against a fake pyudev that yields each device in turn.

    Returns the callback mock and the stop event so tests can assert what fired.
    """
    callback = MagicMock()
    stop = threading.Event()

    fake_monitor = MagicMock()
    iterator = iter(devices)

    def _poll(timeout: float = 0.5) -> object | None:
        try:
            return next(iterator)
        except StopIteration:
            stop.set()
            return None

    fake_monitor.poll.side_effect = _poll

    with (
        patch("windowcharmer.input.udev_monitor.pyudev.Context"),
        patch("windowcharmer.input.udev_monitor.pyudev.Monitor.from_netlink", return_value=fake_monitor),
    ):
        UdevKeyboardMonitor(callback=callback, stop_event=stop).start()

    return callback, stop


def test_callback_fires_on_keyboard_add() -> None:
    callback, _ = _run_monitor([_device()])
    callback.assert_called_once()


def test_non_keyboard_input_ignored() -> None:
    callback, _ = _run_monitor([_device(is_keyboard="0")])
    callback.assert_not_called()


def test_remove_action_ignored() -> None:
    callback, _ = _run_monitor([_device(action="remove")])
    callback.assert_not_called()


def test_non_event_devname_ignored() -> None:
    """A device without DEVNAME starting with /dev/input/event must not trigger."""
    callback, _ = _run_monitor([_device(devname="/dev/input/mouse0")])
    callback.assert_not_called()


def test_stops_when_stop_event_set() -> None:
    callback = MagicMock()
    stop = threading.Event()
    stop.set()  # already set before start

    fake_monitor = MagicMock()
    fake_monitor.poll.return_value = None

    with (
        patch("windowcharmer.input.udev_monitor.pyudev.Context"),
        patch("windowcharmer.input.udev_monitor.pyudev.Monitor.from_netlink", return_value=fake_monitor),
    ):
        UdevKeyboardMonitor(callback=callback, stop_event=stop).start()

    callback.assert_not_called()
    fake_monitor.poll.assert_not_called()  # loop exits before first poll


def test_setup_failure_does_not_raise() -> None:
    """If pyudev.Context() blows up (e.g. missing libudev), start() must
    log and return — not crash the monitor thread.
    """
    callback = MagicMock()
    stop = threading.Event()

    with patch("windowcharmer.input.udev_monitor.pyudev.Context", side_effect=RuntimeError("no udev")):
        UdevKeyboardMonitor(callback=callback, stop_event=stop).start()

    callback.assert_not_called()
