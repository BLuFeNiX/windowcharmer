"""Unit tests for WindowManager that require no real X server connection."""

from unittest.mock import MagicMock, patch

import pytest

from windowcharmer.tiling.manager import FrameExtents, WindowManager


def _make_wm() -> WindowManager:
    """Return a WindowManager with all X11 dependencies mocked out."""
    with (
        patch("windowcharmer.tiling.manager.DisplayPool") as mock_pool,
        patch("windowcharmer.tiling.manager.AtomCache"),
    ):
        mock_screen = MagicMock()
        mock_screen.width_in_pixels = 1920
        mock_screen.height_in_pixels = 1080
        mock_screen.root = MagicMock()
        mock_pool.get_display.return_value.screen.return_value = mock_screen
        return WindowManager()


@pytest.fixture()
def wm() -> WindowManager:
    return _make_wm()


# ---------------------------------------------------------------------------
# get_gtk_frame_extents
# ---------------------------------------------------------------------------


def test_gtk_frame_extents_normal(wm: WindowManager) -> None:
    with patch("windowcharmer.tiling.manager.get_property_value", return_value=[2, 2, 28, 2]):
        result = wm.get_gtk_frame_extents(MagicMock())
    assert result == FrameExtents(left=2, right=2, top=28, bottom=2)


def test_gtk_frame_extents_short_returns_none(wm: WindowManager) -> None:
    # Property with fewer than 4 elements must not IndexError
    with patch("windowcharmer.tiling.manager.get_property_value", return_value=[1, 2, 3]):
        assert wm.get_gtk_frame_extents(MagicMock()) is None


def test_gtk_frame_extents_empty_returns_none(wm: WindowManager) -> None:
    with patch("windowcharmer.tiling.manager.get_property_value", return_value=None):
        assert wm.get_gtk_frame_extents(MagicMock()) is None


# ---------------------------------------------------------------------------
# _update_state  — _NET_WORKAREA fallback
# ---------------------------------------------------------------------------


def test_update_state_uses_workarea_when_present(wm: WindowManager) -> None:
    def _gpv(window: object, atom: object, *_: object) -> list[int] | None:
        if atom is wm.atom.workarea:
            return [0, 40, 1920, 1000]  # x, y, w, h
        return [0]  # current_desktop

    with patch("windowcharmer.tiling.manager.get_property_value", side_effect=_gpv):
        wm._update_state()

    assert wm.dim is not None
    assert wm.dim.wa_y == 40
    assert wm.dim.wa_h == 1000


def test_update_state_falls_back_when_workarea_absent(wm: WindowManager) -> None:
    def _gpv(window: object, atom: object, *_: object) -> list[int] | None:
        if atom is wm.atom.workarea:
            return None
        return [0]  # current_desktop

    with patch("windowcharmer.tiling.manager.get_property_value", side_effect=_gpv):
        wm._update_state()

    assert wm.dim is not None
    assert wm.dim.wa_y == 0
    assert wm.dim.wa_h == 1080  # falls back to screen height


def test_update_state_propagates_exception(wm: WindowManager) -> None:
    wm.d.screen.side_effect = RuntimeError("X gone")
    with pytest.raises(RuntimeError):
        wm._update_state()


def test_execute_action_logs_and_returns_on_update_failure(wm: WindowManager, caplog: pytest.LogCaptureFixture) -> None:
    from windowcharmer.config.actions import TileAction

    wm.d.screen.side_effect = RuntimeError("X gone")
    wm.execute_action(TileAction.LEFT)  # must not raise
    assert any("Error executing action" in r.message for r in caplog.records)
