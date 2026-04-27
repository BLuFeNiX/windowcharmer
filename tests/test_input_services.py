"""Unit tests for InputServices orchestration."""

import threading
from unittest.mock import MagicMock, patch

from windowcharmer.input.services import InputServices


def _make_services() -> InputServices:
    return InputServices(on_rebind_callback=MagicMock(), on_key_event_callback=MagicMock())


def test_start_all_starts_three_threads() -> None:
    """One thread per monitor: sleep, udev, key."""
    services = _make_services()
    with (
        patch("windowcharmer.input.services.WakeFromSleepDetector"),
        patch("windowcharmer.input.services.UdevKeyboardMonitor"),
        patch("windowcharmer.input.services.KeyMonitor"),
        patch("windowcharmer.input.services.DisplayPool"),
    ):
        services.start_all()

    assert len(services._threads) == 3
    for t in services._threads:
        assert t.daemon is True


def test_stop_all_sets_event_and_stops_key_monitor() -> None:
    """stop_all() must propagate the stop signal and disable the XRecord context."""
    services = _make_services()
    services._key_monitor = MagicMock()

    # Real threads that exit when the stop_event is set.
    def _runner() -> None:
        services._stop_event.wait(timeout=2.0)

    services._threads = [threading.Thread(target=_runner, daemon=True) for _ in range(3)]
    for t in services._threads:
        t.start()

    services.stop_all()

    assert services._stop_event.is_set()
    services._key_monitor.stop.assert_called_once()
    for t in services._threads:
        assert not t.is_alive()


def test_stop_all_logs_when_thread_does_not_exit() -> None:
    """A monitor thread that ignores stop_event should be reported, not silently abandoned."""
    services = _make_services()
    services._key_monitor = MagicMock()

    hung = MagicMock(spec=threading.Thread)
    hung.name = "hung-monitor"
    hung.is_alive.return_value = True  # still alive after join() timeout
    services._threads = [hung]

    with patch("windowcharmer.input.services.logger") as log:
        services.stop_all()

    hung.join.assert_called_once_with(timeout=2.0)
    log.warning.assert_called_once()
    assert "hung-monitor" in log.warning.call_args.args
