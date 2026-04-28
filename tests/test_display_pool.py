"""Unit tests for DisplayPool."""

from unittest.mock import MagicMock, patch

from windowcharmer.x11.display_pool import DisplayPool


def _reset_pool() -> None:
    DisplayPool._displays.clear()


def test_get_display_creates_and_caches() -> None:
    _reset_pool()
    fake = MagicMock()
    with patch("windowcharmer.x11.display_pool.display.Display", return_value=fake) as display_cls:
        first = DisplayPool.get_display("wm")
        second = DisplayPool.get_display("wm")
    assert first is fake
    assert second is fake
    assert display_cls.call_count == 1


def test_get_display_separate_per_name() -> None:
    _reset_pool()
    instances = [MagicMock(), MagicMock()]
    with patch("windowcharmer.x11.display_pool.display.Display", side_effect=instances):
        a = DisplayPool.get_display("wm")
        b = DisplayPool.get_display("grabber")
    assert a is instances[0]
    assert b is instances[1]
    assert a is not b


def test_close_all_closes_each_and_clears_cache() -> None:
    _reset_pool()
    a, b = MagicMock(), MagicMock()
    with patch("windowcharmer.x11.display_pool.display.Display", side_effect=[a, b]):
        DisplayPool.get_display("wm")
        DisplayPool.get_display("grabber")
    DisplayPool.close_all()
    a.close.assert_called_once()
    b.close.assert_called_once()
    assert DisplayPool._displays == {}


def test_close_all_swallows_close_errors() -> None:
    _reset_pool()
    bad = MagicMock()
    bad.close.side_effect = RuntimeError("already closed")
    good = MagicMock()
    with patch("windowcharmer.x11.display_pool.display.Display", side_effect=[bad, good]):
        DisplayPool.get_display("wm")
        DisplayPool.get_display("grabber")
    DisplayPool.close_all()  # must not raise
    good.close.assert_called_once()
    assert DisplayPool._displays == {}
