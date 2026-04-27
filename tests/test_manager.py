"""Unit tests for WindowManager that require no real X server connection."""

from unittest.mock import MagicMock, patch

import pytest

from windowcharmer.tiling.manager import FrameExtents, WindowManager, _remap_zone_no_center


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
    assert wm.dim.wa_x == 0
    assert wm.dim.wa_y == 40
    assert wm.dim.wa_w == 1920
    assert wm.dim.wa_h == 1000


def test_update_state_honors_left_panel(wm: WindowManager) -> None:
    """Regression: a left panel reports wa_x=60, wa_w=1860 in _NET_WORKAREA.
    The previous code only unpacked y/h, so left tiling overlapped the panel.
    """

    def _gpv(window: object, atom: object, *_: object) -> list[int] | None:
        if atom is wm.atom.workarea:
            return [60, 0, 1860, 1080]  # 60px left panel
        return [0]

    with patch("windowcharmer.tiling.manager.get_property_value", side_effect=_gpv):
        wm._update_state()

    assert wm.dim is not None
    assert wm.dim.wa_x == 60
    assert wm.dim.wa_w == 1860
    # The leftmost zone now starts at the panel boundary, not the screen edge.
    assert wm.dim.x_left == 60


def test_update_state_falls_back_when_workarea_absent(wm: WindowManager) -> None:
    def _gpv(window: object, atom: object, *_: object) -> list[int] | None:
        if atom is wm.atom.workarea:
            return None
        return [0]  # current_desktop

    with patch("windowcharmer.tiling.manager.get_property_value", side_effect=_gpv):
        wm._update_state()

    assert wm.dim is not None
    assert wm.dim.wa_x == 0
    assert wm.dim.wa_y == 0
    assert wm.dim.wa_w == 1920  # falls back to screen width
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
    wm.dim = ScreenDimensions(0, 40, 1920, 1000, 768)
    wm.config._desktop_ratios = {0: 2}  # ratio_idx 2 → non-zero center
    wm.config.set_state(1920, 0)  # populate wa_w so center_width is computed
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

    wm.dim = ScreenDimensions(0, 40, 1920, 1000, 0)  # center_width = 0
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


# ---------------------------------------------------------------------------
# _remap_zone_no_center
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "zone, expected",
    [
        ("left-center", "left"),
        ("right-center", "right"),
        ("center", "left"),
        ("top-center", "top-left"),
        ("bottom-center", "bottom-left"),
        # Side-only zones pass through unchanged
        ("left", "left"),
        ("right", "right"),
        ("top-left", "top-left"),
        ("bottom-right", "bottom-right"),
        # Non-tileable / unknown zones pass through
        ("unknown", "unknown"),
        ("top-left-center", "top-left-center"),
    ],
)
def test_remap_zone_no_center(zone: str, expected: str) -> None:
    assert _remap_zone_no_center(zone) == expected


def test_resize_all_windows_includes_sticky_window() -> None:
    """Sticky windows have _NET_WM_DESKTOP == 0xFFFFFFFF and must be tiled on every desktop."""
    wm = _make_wm_with_dim()
    wm.config.active_desktop = 1
    win = MagicMock()

    with (
        patch.object(wm, "list_windows", return_value=[win]),
        patch.object(wm, "get_window_desktop", return_value=0xFFFFFFFF),
        patch.object(wm, "is_window_maximized_vertically", return_value=False),
        patch.object(wm, "_update_state"),
        patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"),
        patch.object(wm, "_apply_tile_action") as apply_action,
    ):
        wm.resize_all_windows(1)

    apply_action.assert_called_once()


# ---------------------------------------------------------------------------
# _compute_client_geometry — frame extents
# ---------------------------------------------------------------------------


def test_compute_client_geometry_no_frame_extents(wm: WindowManager) -> None:
    """Bare X11 window with no decorations: target rect passes through unchanged."""
    win = MagicMock()
    with (
        patch.object(wm, "get_gtk_frame_extents", return_value=None),
        patch("windowcharmer.tiling.manager.get_property_value", return_value=None),
    ):
        x, y, w, h = wm._compute_client_geometry(win, 100, 50, 800, 600)
    assert (x, y, w, h) == (100, 50, 800, 600)


def test_compute_client_geometry_gtk_csd_only(wm: WindowManager) -> None:
    """GTK CSD: shadows live inside the X11 window. Origin shifts out, size grows."""
    win = MagicMock()
    fe = FrameExtents(left=16, right=16, top=10, bottom=20)
    with (
        patch.object(wm, "get_gtk_frame_extents", return_value=fe),
        patch("windowcharmer.tiling.manager.get_property_value", return_value=None),
    ):
        x, y, w, h = wm._compute_client_geometry(win, 100, 50, 800, 600)
    assert x == 100 - 16
    assert y == 50 - 10
    assert w == 800 + 16 + 16
    assert h == 600 + 10 + 20


def test_compute_client_geometry_net_frame_only(wm: WindowManager) -> None:
    """WM-decorated: titlebar+borders are outside the X11 client. Size shrinks; origin unchanged."""
    win = MagicMock()
    with (
        patch.object(wm, "get_gtk_frame_extents", return_value=None),
        patch("windowcharmer.tiling.manager.get_property_value", return_value=[2, 2, 28, 2]),
    ):
        x, y, w, h = wm._compute_client_geometry(win, 100, 50, 800, 600)
    assert x == 100
    assert y == 50
    assert w == 800 - 4
    assert h == 600 - 30


def test_compute_client_geometry_combined_extents(wm: WindowManager) -> None:
    """Pathological case: both _GTK_FRAME_EXTENTS and _NET_FRAME_EXTENTS set.
    Verify the math is order-stable (GTK shift+grow first, then NET shrink).
    """
    win = MagicMock()
    gtk = FrameExtents(left=16, right=16, top=10, bottom=20)
    net = [2, 2, 4, 2]  # left, right, top, bottom
    with (
        patch.object(wm, "get_gtk_frame_extents", return_value=gtk),
        patch("windowcharmer.tiling.manager.get_property_value", return_value=net),
    ):
        x, y, w, h = wm._compute_client_geometry(win, 100, 50, 800, 600)
    assert x == 100 - 16
    assert y == 50 - 10
    assert w == (800 + 16 + 16) - (2 + 2)
    assert h == (600 + 10 + 20) - (4 + 2)


def test_compute_client_geometry_clamps_to_one(wm: WindowManager) -> None:
    """If frame extents would shrink the result to 0 or negative, clamp to 1px."""
    win = MagicMock()
    with (
        patch.object(wm, "get_gtk_frame_extents", return_value=None),
        patch("windowcharmer.tiling.manager.get_property_value", return_value=[500, 500, 500, 500]),
    ):
        _, _, w, h = wm._compute_client_geometry(win, 0, 0, 100, 100)
    assert w == 1
    assert h == 1


# ---------------------------------------------------------------------------
# move_and_resize — clears max flags before configure
# ---------------------------------------------------------------------------


def test_move_and_resize_clears_max_flags_when_maximized(wm: WindowManager) -> None:
    """A maximized window ignores configure(); the WM flags must be cleared first."""
    win = MagicMock()
    with (
        patch.object(wm, "_compute_client_geometry", return_value=(0, 0, 800, 600)),
        patch.object(wm, "is_window_maximized_vertically", return_value=True),
        patch.object(wm, "is_window_maximized_horizontally", return_value=False),
        patch.object(wm, "set_max_flags") as set_flags,
    ):
        wm.move_and_resize(win, 0, 0, 800, 600)
    set_flags.assert_called_once_with(win, 0, 0)
    win.configure.assert_called_once()


def test_move_and_resize_skips_clear_when_not_maximized(wm: WindowManager) -> None:
    win = MagicMock()
    with (
        patch.object(wm, "_compute_client_geometry", return_value=(10, 20, 100, 200)),
        patch.object(wm, "is_window_maximized_vertically", return_value=False),
        patch.object(wm, "is_window_maximized_horizontally", return_value=False),
        patch.object(wm, "set_max_flags") as set_flags,
    ):
        wm.move_and_resize(win, 10, 20, 100, 200)
    set_flags.assert_not_called()
    win.configure.assert_called_once()
    kwargs = win.configure.call_args.kwargs
    assert kwargs["x"] == 10
    assert kwargs["y"] == 20
    assert kwargs["width"] == 100
    assert kwargs["height"] == 200
