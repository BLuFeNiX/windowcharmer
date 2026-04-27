"""Unit tests for KeyMonitor."""

from unittest.mock import MagicMock, patch

import pytest

from windowcharmer.input.key_monitor import KeyMonitor


def test_start_raises_when_record_extension_missing() -> None:
    """Without the RECORD extension we can't tap key events at all — surface that."""
    dpy = MagicMock()
    dpy.has_extension.return_value = False

    monitor = KeyMonitor(dpy=dpy, callback=MagicMock())
    with pytest.raises(OSError, match="RECORD"):
        monitor.start()


def test_stop_is_noop_when_ctx_is_none() -> None:
    """A stop() call before start() (or after a prior stop) must not crash."""
    monitor = KeyMonitor(dpy=MagicMock(), callback=MagicMock())
    assert monitor.ctx is None

    with patch("windowcharmer.input.key_monitor.Display") as Display:
        monitor.stop()

    Display.assert_not_called()


def test_stop_uses_fresh_display_to_disable_context() -> None:
    """python-xlib requires record_disable_context to come from a different
    Display than record_enable_context, so stop() opens a throwaway connection.
    """
    monitor = KeyMonitor(dpy=MagicMock(), callback=MagicMock())
    monitor.ctx = MagicMock()  # pretend a context is live

    fake_display = MagicMock()
    with patch("windowcharmer.input.key_monitor.Display", return_value=fake_display) as Display:
        monitor.stop()

    Display.assert_called_once_with()
    fake_display.record_disable_context.assert_called_once_with(monitor.ctx)
    fake_display.flush.assert_called_once()
    fake_display.close.assert_called_once()


def test_stop_swallows_display_open_failure() -> None:
    """If we can't open a fresh Display (X server gone), don't crash the caller."""
    monitor = KeyMonitor(dpy=MagicMock(), callback=MagicMock())
    monitor.ctx = MagicMock()

    with patch("windowcharmer.input.key_monitor.Display", side_effect=RuntimeError("no display")):
        monitor.stop()  # must not raise


def test_stop_closes_display_even_when_disable_fails() -> None:
    """Disable might fail (e.g. context already torn down) but the throwaway
    Display still needs to be closed so we don't leak X connections.
    """
    monitor = KeyMonitor(dpy=MagicMock(), callback=MagicMock())
    monitor.ctx = MagicMock()

    fake_display = MagicMock()
    fake_display.record_disable_context.side_effect = RuntimeError("already torn down")

    with patch("windowcharmer.input.key_monitor.Display", return_value=fake_display):
        monitor.stop()

    fake_display.close.assert_called_once()
