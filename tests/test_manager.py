"""Unit tests for WindowManager that require no real X server connection.

These tests exercise tiling policy and geometry against a mocked
EwmhClient — the X11 wire is not the test boundary, the EwmhClient
interface is.
"""

from unittest.mock import MagicMock, patch

import pytest

from windowcharmer.tiling.manager import WindowManager, _remap_zone_no_center
from windowcharmer.x11.ewmh_client import FrameExtents


def _make_wm() -> WindowManager:
    """Return a WindowManager with all X11 dependencies mocked out."""
    with (
        patch("windowcharmer.tiling.manager.DisplayPool"),
        patch("windowcharmer.tiling.manager.EwmhClient") as ewmh_cls,
    ):
        ewmh = ewmh_cls.return_value
        # Sensible defaults so unrelated tests don't trip on attribute access.
        ewmh.get_screen_size.return_value = (1920, 1080)
        ewmh.get_workarea.return_value = None
        ewmh.get_active_desktop.return_value = 0
        return WindowManager()


@pytest.fixture()
def wm() -> WindowManager:
    return _make_wm()


# ---------------------------------------------------------------------------
# _update_state  — _NET_WORKAREA fallback
# ---------------------------------------------------------------------------


def test_update_state_uses_workarea_when_present(wm: WindowManager) -> None:
    wm.props.get_workarea.return_value = (0, 40, 1920, 1000)
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
    wm.props.get_workarea.return_value = (60, 0, 1860, 1080)
    wm._update_state()

    assert wm.dim is not None
    assert wm.dim.wa_x == 60
    assert wm.dim.wa_w == 1860
    # The leftmost zone now starts at the panel boundary, not the screen edge.
    assert wm.dim.x_left == 60


def test_update_state_falls_back_when_workarea_absent(wm: WindowManager) -> None:
    wm.props.get_workarea.return_value = None
    wm.props.get_screen_size.return_value = (1920, 1080)
    wm._update_state()

    assert wm.dim is not None
    assert wm.dim.wa_x == 0
    assert wm.dim.wa_y == 0
    assert wm.dim.wa_w == 1920  # falls back to screen width
    assert wm.dim.wa_h == 1080  # falls back to screen height


def test_update_state_propagates_exception(wm: WindowManager) -> None:
    wm.props.get_active_desktop.side_effect = RuntimeError("X gone")
    with pytest.raises(RuntimeError):
        wm._update_state()


def test_execute_action_logs_and_returns_on_update_failure(wm: WindowManager, caplog: pytest.LogCaptureFixture) -> None:
    from windowcharmer.config.actions import TileAction

    wm.props.get_active_desktop.side_effect = RuntimeError("X gone")
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
    wm.props.is_window_maximized_vertically.return_value = False
    win = MagicMock()

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value=start_zone):
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
# _compute_client_geometry — frame extents
# ---------------------------------------------------------------------------


def test_compute_client_geometry_no_frame_extents(wm: WindowManager) -> None:
    """Bare X11 window with no decorations: target rect passes through unchanged."""
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = None
    x, y, w, h = wm._compute_client_geometry(MagicMock(), 100, 50, 800, 600)
    assert (x, y, w, h) == (100, 50, 800, 600)


def test_compute_client_geometry_gtk_csd_only(wm: WindowManager) -> None:
    """GTK CSD: shadows live inside the X11 window. Origin shifts out, size grows."""
    wm.props.get_gtk_frame_extents.return_value = FrameExtents(left=16, right=16, top=10, bottom=20)
    wm.props.get_net_frame_extents.return_value = None
    x, y, w, h = wm._compute_client_geometry(MagicMock(), 100, 50, 800, 600)
    assert x == 100 - 16
    assert y == 50 - 10
    assert w == 800 + 16 + 16
    assert h == 600 + 10 + 20


def test_compute_client_geometry_net_frame_only(wm: WindowManager) -> None:
    """WM-decorated: titlebar+borders are outside the X11 client. Size shrinks; origin unchanged."""
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = FrameExtents(left=2, right=2, top=28, bottom=2)
    x, y, w, h = wm._compute_client_geometry(MagicMock(), 100, 50, 800, 600)
    assert x == 100
    assert y == 50
    assert w == 800 - 4
    assert h == 600 - 30


def test_compute_client_geometry_combined_extents(wm: WindowManager) -> None:
    """Pathological case: both _GTK_FRAME_EXTENTS and _NET_FRAME_EXTENTS set.
    Verify the math is order-stable (GTK shift+grow first, then NET shrink).
    """
    wm.props.get_gtk_frame_extents.return_value = FrameExtents(left=16, right=16, top=10, bottom=20)
    wm.props.get_net_frame_extents.return_value = FrameExtents(left=2, right=2, top=4, bottom=2)
    x, y, w, h = wm._compute_client_geometry(MagicMock(), 100, 50, 800, 600)
    assert x == 100 - 16
    assert y == 50 - 10
    assert w == (800 + 16 + 16) - (2 + 2)
    assert h == (600 + 10 + 20) - (4 + 2)


def test_compute_client_geometry_clamps_to_one(wm: WindowManager) -> None:
    """If frame extents would shrink the result to 0 or negative, clamp to 1px."""
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = FrameExtents(left=500, right=500, top=500, bottom=500)
    _, _, w, h = wm._compute_client_geometry(MagicMock(), 0, 0, 100, 100)
    assert w == 1
    assert h == 1


# ---------------------------------------------------------------------------
# move_and_resize — clears max flags before configure
# ---------------------------------------------------------------------------


def test_move_and_resize_clears_max_flags_when_maximized(wm: WindowManager) -> None:
    """A maximized window ignores configure(); the WM flags must be cleared first."""
    win = MagicMock()
    wm.props.is_window_maximized_vertically.return_value = True
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False
    with patch.object(wm, "_compute_client_geometry", return_value=(0, 0, 800, 600)):
        wm.move_and_resize(win, 0, 0, 800, 600)
    wm.props.set_max_flags.assert_called_once_with(win, 0, 0)
    win.configure.assert_called_once()


def test_move_and_resize_skips_clear_when_not_maximized(wm: WindowManager) -> None:
    win = MagicMock()
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False
    with patch.object(wm, "_compute_client_geometry", return_value=(10, 20, 100, 200)):
        wm.move_and_resize(win, 10, 20, 100, 200)
    wm.props.set_max_flags.assert_not_called()
    wm.props.set_fullscreen_flag.assert_not_called()
    win.configure.assert_called_once()
    kwargs = win.configure.call_args.kwargs
    assert kwargs["x"] == 10
    assert kwargs["y"] == 20
    assert kwargs["width"] == 100
    assert kwargs["height"] == 200


def test_move_and_resize_clears_fullscreen_flag_when_fullscreen(wm: WindowManager) -> None:
    """A fullscreen window ignores configure() — the WM owns its geometry while
    the flag is set. Without the clear, the configure is rejected (or briefly
    applied then snapped back, producing a flicker) and the window stays
    fullscreen.
    """
    win = MagicMock()
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = True
    with patch.object(wm, "_compute_client_geometry", return_value=(0, 0, 800, 600)):
        wm.move_and_resize(win, 0, 0, 800, 600)
    wm.props.set_fullscreen_flag.assert_called_once_with(win, on=False)
    win.configure.assert_called_once()


def test_move_and_resize_clears_both_when_max_and_fullscreen() -> None:
    """A window can carry both maximize and fullscreen flags simultaneously
    (some apps toggle fullscreen on a previously-maximized window). Clear both
    before configure, otherwise either residual flag keeps the WM in charge.
    """
    wm = _make_wm()
    win = MagicMock()
    wm.props.is_window_maximized_vertically.return_value = True
    wm.props.is_window_maximized_horizontally.return_value = True
    wm.props.is_window_fullscreen.return_value = True
    with patch.object(wm, "_compute_client_geometry", return_value=(0, 0, 800, 600)):
        wm.move_and_resize(win, 0, 0, 800, 600)
    wm.props.set_max_flags.assert_called_once_with(win, 0, 0)
    wm.props.set_fullscreen_flag.assert_called_once_with(win, on=False)


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
    wm.props.list_windows.return_value = [win]
    wm.props.get_window_desktop.return_value = 0
    wm.props.is_window_maximized_vertically.return_value = False

    with (
        patch.object(wm, "_update_state"),
        patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="top-left-center"),
        patch.object(wm, "move_and_resize") as move_and_resize,
    ):
        wm.resize_all_windows(1)  # must not raise

    move_and_resize.assert_not_called()


def test_resize_all_windows_includes_sticky_window() -> None:
    """Sticky windows have _NET_WM_DESKTOP == 0xFFFFFFFF and must be tiled on every desktop."""
    wm = _make_wm_with_dim()
    wm.config.active_desktop = 1
    win = MagicMock()
    wm.props.list_windows.return_value = [win]
    wm.props.get_window_desktop.return_value = 0xFFFFFFFF
    wm.props.is_window_maximized_vertically.return_value = False

    with (
        patch.object(wm, "_update_state"),
        patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"),
        patch.object(wm, "move_and_resize") as move_and_resize,
    ):
        wm.resize_all_windows(1)

    move_and_resize.assert_called_once()


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


# ---------------------------------------------------------------------------
# _apply_tile_action — center-needing actions in two-column mode
# ---------------------------------------------------------------------------


def test_apply_tile_action_warns_and_skips_when_center_required_but_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Pressing CENTER (or any needs_center action) when center_width==0 must
    log a warning rather than silently no-op — otherwise the user has no signal
    that the keypress was acknowledged but ignored.
    """
    import logging

    from windowcharmer.config.actions import TileAction
    from windowcharmer.config.dimensions import ScreenDimensions

    wm = _make_wm()
    wm.dim = ScreenDimensions(0, 0, 1920, 1080, 0)  # center_width = 0
    wm.config.center_width = 0
    win = MagicMock()

    caplog.set_level(logging.WARNING, logger="windowcharmer.tiling.manager")
    with patch.object(wm, "move_and_resize") as move_and_resize:
        wm._apply_tile_action(TileAction.CENTER, win)

    move_and_resize.assert_not_called()
    assert any("center column" in record.message and "CENTER" in record.message.upper() for record in caplog.records), (
        f"expected warning about center-required action; got {[r.message for r in caplog.records]}"
    )


# ---------------------------------------------------------------------------
# _try_animated_tile / _apply_tile_action — no _NET_WM_STATE writes for tiles
# ---------------------------------------------------------------------------


def test_apply_tile_action_does_not_set_max_flags_for_tile(wm: WindowManager) -> None:
    """Tile actions must not direct-write _NET_WM_STATE — that path is reserved
    for MAX/RESTORE. Direct-writing on tiles caused Muffin focus-stack and
    repositioning churn (visible as window shake / focus jumps to previous).
    """
    from windowcharmer.config.actions import TileAction
    from windowcharmer.config.dimensions import ScreenDimensions

    wm.dim = ScreenDimensions(0, 0, 1920, 1080, 0)
    wm.config.center_width = 0
    win = MagicMock()

    with patch.object(wm, "move_and_resize") as move_and_resize:
        wm._apply_tile_action(TileAction.LEFT, win)

    move_and_resize.assert_called_once()
    wm.props.set_max_flags.assert_not_called()


def test_apply_tile_action_sets_max_flags_for_max_action(wm: WindowManager) -> None:
    """MAX is a flag-only action — it must set both max flags."""
    from windowcharmer.config.actions import TileAction

    win = MagicMock()
    wm._apply_tile_action(TileAction.MAX, win)
    wm.props.set_max_flags.assert_called_once_with(win, 1, 1)


def test_apply_tile_action_sets_max_flags_for_restore_action(wm: WindowManager) -> None:
    """RESTORE clears both max flags."""
    from windowcharmer.config.actions import TileAction

    win = MagicMock()
    wm._apply_tile_action(TileAction.RESTORE, win)
    wm.props.set_max_flags.assert_called_once_with(win, 0, 0)
