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


# ---------------------------------------------------------------------------
# _resolve_tile_cycle
# ---------------------------------------------------------------------------


def _make_wm_with_dim() -> WindowManager:
    """WindowManager with a real ScreenDimensions (1920x1080, 40px panel, 768px center)."""
    from windowcharmer.config.dimensions import ScreenDimensions

    wm = _make_wm()
    wm.dim = ScreenDimensions(1920, 40, 1000, 768)
    wm.config._desktop_ratios = {0: 2}  # ratio_idx 2 → non-zero center
    return wm


def _mock_win_in_zone(zone_str: str) -> MagicMock:
    """Return a mock window whose determine_tile_zone result is patched to zone_str."""
    win = MagicMock()
    win._zone_str = zone_str
    return win


@pytest.mark.parametrize(
    "start_zone, action_in, action_out",
    [
        # Left cycling
        ("unknown", "left", "left"),
        ("left", "left", "left-center"),
        ("left-center", "left", "left"),
        # Right cycling
        ("unknown", "right", "right"),
        ("right", "right", "right-center"),
        ("right-center", "right", "right"),
        # Unrelated actions pass through unchanged
        ("left", "center", "center"),
        ("right", "max", "max"),
    ],
)
def test_resolve_tile_cycle(start_zone: str, action_in: str, action_out: str) -> None:
    from windowcharmer.config.actions import TileAction

    wm = _make_wm_with_dim()
    win = MagicMock()

    with (
        patch("windowcharmer.tiling.manager.determine_tile_zone", return_value=start_zone),
        patch.object(wm, "is_window_maximized_vertically", return_value=False),
    ):
        result = wm._resolve_tile_cycle(TileAction(action_in), win)

    assert result == TileAction(action_out)


def test_resolve_tile_cycle_skipped_when_no_center(wm: WindowManager) -> None:
    """Cycling must not trigger when center_width == 0 (two-column mode)."""
    from windowcharmer.config.actions import TileAction
    from windowcharmer.config.dimensions import ScreenDimensions

    wm.dim = ScreenDimensions(1920, 40, 1000, 0)  # center_width = 0
    wm.config.center_width = 0
    win = MagicMock()

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        result = wm._resolve_tile_cycle(TileAction.LEFT, win)

    assert result == TileAction.LEFT


# ---------------------------------------------------------------------------
# get_active_window
# ---------------------------------------------------------------------------


def test_get_active_window_returns_none_for_zero_id(wm: WindowManager) -> None:
    """_NET_ACTIVE_WINDOW = [0] means 'no active window'; do not return a Window resource for ID 0."""
    with patch("windowcharmer.tiling.manager.get_property_value", return_value=[0]):
        assert wm.get_active_window() is None


def test_get_active_window_returns_none_when_property_absent(wm: WindowManager) -> None:
    with patch("windowcharmer.tiling.manager.get_property_value", return_value=None):
        assert wm.get_active_window() is None


def test_get_active_window_creates_resource_for_real_id(wm: WindowManager) -> None:
    sentinel = object()
    wm.d.create_resource_object.return_value = sentinel
    with patch("windowcharmer.tiling.manager.get_property_value", return_value=[0x4200001]):
        assert wm.get_active_window() is sentinel
    wm.d.create_resource_object.assert_called_once_with("window", 0x4200001)


# ---------------------------------------------------------------------------
# resize_all_windows
# ---------------------------------------------------------------------------


def test_resize_all_windows_skips_invalid_zone() -> None:
    """A multi-column half-height window (zone 'top-left-center') is not a valid
    TileAction but passes the unknown filter; resize_all_windows must skip it
    rather than crashing the whole resize pass.
    """
    wm = _make_wm_with_dim()
    win = MagicMock()

    with (
        patch.object(wm, "list_windows", return_value=[win]),
        patch.object(wm, "get_window_desktop", return_value=0),
        patch.object(wm, "is_window_maximized_vertically", return_value=False),
        patch.object(wm, "_update_state"),
        patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="top-left-center"),
        patch.object(wm, "_apply_tile_action") as apply_action,
    ):
        wm.resize_all_windows(1)  # must not raise

    apply_action.assert_not_called()
