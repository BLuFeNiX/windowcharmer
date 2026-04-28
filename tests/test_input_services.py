"""Unit tests for InputServices orchestration."""

import threading
from unittest.mock import MagicMock, patch

from windowcharmer.input.services import InputServices


def _make_services() -> InputServices:
    return InputServices(on_rebind_callback=MagicMock())


def test_start_all_starts_sleep_monitor_thread() -> None:
    services = _make_services()
    with patch("windowcharmer.input.services.WakeFromSleepDetector"):
        services.start_all()

    assert len(services._threads) == 1
    assert services._threads[0].daemon is True


def test_stop_all_propagates_stop_event_and_joins() -> None:
    services = _make_services()

    def _runner() -> None:
        services._stop_event.wait(timeout=2.0)

    services._threads = [threading.Thread(target=_runner, daemon=True)]
    services._threads[0].start()

    services.stop_all()

    assert services._stop_event.is_set()
    assert not services._threads[0].is_alive()


def test_stop_all_warns_when_thread_does_not_exit() -> None:
    services = _make_services()

    hung = MagicMock(spec=threading.Thread)
    hung.name = "hung-monitor"
    hung.is_alive.return_value = True
    services._threads = [hung]

    with patch("windowcharmer.input.services.logger") as log:
        services.stop_all()

    hung.join.assert_called_once_with(timeout=2.0)
    log.warning.assert_called_once()
    assert "hung-monitor" in log.warning.call_args.args
