"""Unit tests for WindowManager that require no real X server connection.

These tests exercise tiling policy and geometry against a mocked
EwmhClient — the X11 wire is not the test boundary, the EwmhClient
interface is.
"""

from unittest.mock import ANY, MagicMock, patch

import pytest

from windowcharmer.tiling.manager import WindowManager, _remap_zone_no_center
from windowcharmer.x11.ewmh_client import FrameExtents


def _make_wm() -> WindowManager:
    """Return a WindowManager with all X11 dependencies mocked out."""
    with (
        patch("windowcharmer.tiling.manager.DisplayPool"),
        patch("windowcharmer.tiling.manager.EwmhClient") as ewmh_cls,
        patch("windowcharmer.tiling.manager.CinnamonAnimator") as animator_cls,
    ):
        ewmh = ewmh_cls.return_value
        # Sensible defaults so unrelated tests don't trip on attribute access.
        ewmh.get_screen_size.return_value = (1920, 1080)
        ewmh.get_workarea.return_value = None
        ewmh.get_active_desktop.return_value = 0
        # Default: animator is "unavailable" — animate/activate return False
        # so non-animated paths run. Tests that need the animator to handle
        # the action override these on a per-test basis.
        animator = animator_cls.return_value
        animator.animate.return_value = False
        animator.animate_batch.return_value = False
        animator.activate.return_value = False
        return WindowManager()


@pytest.fixture
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
    ("start_zone", "action_in", "action_out"),
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
    ("zone", "expected"),
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


def test_apply_tile_action_restore_routes_to_restore_window(wm: WindowManager) -> None:
    """RESTORE goes through _restore_window (spawn-geometry path), not the
    flag-only branch — clearing max flags is now _restore_window's job."""
    from windowcharmer.config.actions import TileAction

    win = MagicMock()
    with patch.object(wm, "_restore_window") as restore:
        wm._apply_tile_action(TileAction.RESTORE, win)
    restore.assert_called_once_with(win)
    wm.props.set_max_flags.assert_not_called()


# ---------------------------------------------------------------------------
# Spawn-geometry tracking
# ---------------------------------------------------------------------------


def _stub_window(wid: int, x: int, y: int, w: int, h: int) -> MagicMock:
    """A fake window whose get_geometry/translate_coords return the given rect.

    translate_coords returns negative coords (X11 inversion); zones.py and
    _track_windows both apply abs() to recover screen position.
    """
    win = MagicMock()
    win.id = wid
    geom = MagicMock(width=w, height=h, root=MagicMock())
    win.get_geometry.return_value = geom
    coords = MagicMock(x=-x, y=-y)
    win.translate_coords.return_value = coords
    return win


def test_track_windows_snapshots_new_window(wm: WindowManager) -> None:
    win = _stub_window(0x42, 100, 200, 800, 600)
    wm.props.list_windows.return_value = [win]

    wm._track_windows()

    assert wm._spawn_geom == {0x42: (100, 200, 800, 600)}


def test_track_windows_refreshes_snapshot_when_window_in_natural_state(wm: WindowManager) -> None:
    """Manual drag-resize: the user moves a tracked window to a geometry that
    doesn't match any tile zone. Refresh the snapshot so RESTORE returns there."""
    from windowcharmer.config.dimensions import ScreenDimensions

    wm.dim = ScreenDimensions(0, 40, 1920, 1000, 768)
    wm.props.is_window_maximized_vertically.return_value = False
    win = _stub_window(0x42, 300, 300, 400, 400)  # nowhere near any tile zone
    wm.props.list_windows.return_value = [win]
    wm._spawn_geom[0x42] = (100, 200, 800, 600)

    wm._track_windows()

    assert wm._spawn_geom == {0x42: (300, 300, 400, 400)}


def test_track_windows_preserves_snapshot_when_window_in_tile_zone(wm: WindowManager) -> None:
    """A window observed at tile-zone geometry is in a state we caused — keep
    the original snapshot so RESTORE goes back to the pre-tile state."""
    from windowcharmer.config.dimensions import ScreenDimensions

    wm.dim = ScreenDimensions(0, 40, 1920, 1000, 768)
    wm.props.is_window_maximized_vertically.return_value = False
    # LEFT zone: x_left, y_top, w_side, h_full.
    win = _stub_window(0x42, wm.dim.x_left, wm.dim.y_top, wm.dim.w_side, wm.dim.h_full)
    wm.props.list_windows.return_value = [win]
    wm._spawn_geom[0x42] = (100, 200, 800, 600)

    wm._track_windows()

    assert wm._spawn_geom == {0x42: (100, 200, 800, 600)}


def test_track_windows_skips_natural_state_check_when_dim_unset(wm: WindowManager) -> None:
    """Without dim, classify_zone has no reference frame — preserve snapshot."""
    wm.dim = None
    win = _stub_window(0x42, 300, 300, 400, 400)
    wm.props.list_windows.return_value = [win]
    wm._spawn_geom[0x42] = (100, 200, 800, 600)

    wm._track_windows()

    assert wm._spawn_geom == {0x42: (100, 200, 800, 600)}


def test_track_windows_skips_refresh_when_geometry_unchanged(wm: WindowManager) -> None:
    """No-op fast path: if cur == existing, don't bother classifying."""
    from windowcharmer.config.dimensions import ScreenDimensions

    wm.dim = ScreenDimensions(0, 40, 1920, 1000, 768)
    win = _stub_window(0x42, 100, 200, 800, 600)
    wm.props.list_windows.return_value = [win]
    wm._spawn_geom[0x42] = (100, 200, 800, 600)

    wm._track_windows()

    # is_window_maximized_vertically should not be queried — fast path bails first.
    wm.props.is_window_maximized_vertically.assert_not_called()
    assert wm._spawn_geom == {0x42: (100, 200, 800, 600)}


def test_track_windows_prunes_dead_windows(wm: WindowManager) -> None:
    """Vanished window IDs must be dropped so reused IDs don't inherit old snapshots."""
    wm._spawn_geom = {0x42: (1, 2, 3, 4), 0x99: (5, 6, 7, 8)}
    wm.props.list_windows.return_value = [_stub_window(0x42, 1, 2, 3, 4)]

    wm._track_windows()

    assert wm._spawn_geom == {0x42: (1, 2, 3, 4)}


def test_track_windows_skips_windows_that_vanish_mid_query(wm: WindowManager) -> None:
    """A window that BadWindows from get_geometry is silently skipped."""
    from Xlib.error import BadWindow

    err = BadWindow.__new__(BadWindow)
    err._data = {"resource_id": 0, "sequence_number": 0, "major_opcode": 0, "minor_opcode": 0}

    win = MagicMock()
    win.id = 0x42
    win.get_geometry.side_effect = err
    wm.props.list_windows.return_value = [win]

    wm._track_windows()  # must not raise

    assert wm._spawn_geom == {}


def test_restore_window_no_snapshot_is_noop(wm: WindowManager) -> None:
    win = MagicMock()
    win.id = 0x42
    wm._spawn_geom = {}

    wm._restore_window(win)

    win.configure.assert_not_called()
    wm.props.set_max_flags.assert_not_called()
    wm.props.set_fullscreen_flag.assert_not_called()


def test_restore_window_applies_snapshot_geometry(wm: WindowManager) -> None:
    win = MagicMock()
    win.id = 0x42
    wm._spawn_geom = {0x42: (100, 200, 800, 600)}
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False

    wm._restore_window(win)

    win.configure.assert_called_once()
    kwargs = win.configure.call_args.kwargs
    assert kwargs["x"] == 100
    assert kwargs["y"] == 200
    assert kwargs["width"] == 800
    assert kwargs["height"] == 600


def test_restore_window_clears_max_flags_when_window_was_maximized(wm: WindowManager) -> None:
    """If the user manually maximized via the WM, RESTORE must clear those
    flags before configuring — WMs reject configure() while max is set."""
    win = MagicMock()
    win.id = 0x42
    wm._spawn_geom = {0x42: (100, 200, 800, 600)}
    wm.props.is_window_maximized_vertically.return_value = True
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False

    wm._restore_window(win)

    wm.props.set_max_flags.assert_called_once_with(win, 0, 0)
    win.configure.assert_called_once()


def test_restore_window_clears_fullscreen_flag_when_set(wm: WindowManager) -> None:
    win = MagicMock()
    win.id = 0x42
    wm._spawn_geom = {0x42: (100, 200, 800, 600)}
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = True

    wm._restore_window(win)

    wm.props.set_fullscreen_flag.assert_called_once_with(win, on=False)


def test_restore_after_tile_returns_to_pre_tile_geometry(wm: WindowManager) -> None:
    """End-to-end: open window at (100,200,800,600), tile LEFT, RESTORE.
    The window must end up back at the original geometry."""
    from windowcharmer.config.actions import TileAction

    win = _stub_window(0x42, 100, 200, 800, 600)
    wm.props.list_windows.return_value = [win]
    wm.props.get_active_window.return_value = win
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False
    wm.props.get_workarea.return_value = (0, 40, 1920, 1000)
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = None

    # First action: LEFT tile. Snapshot should capture (100, 200, 800, 600).
    wm.execute_action(TileAction.LEFT)
    assert wm._spawn_geom[0x42] == (100, 200, 800, 600)

    # Second action: RESTORE. Window.configure should land on snapshot values.
    win.configure.reset_mock()
    wm.execute_action(TileAction.RESTORE)

    win.configure.assert_called()
    # Multiple configure() calls happen: the geometry restore plus a final
    # stack_mode=Above raise. Pick out the geometry call by the keys it sets.
    geom_calls = [c for c in win.configure.call_args_list if "x" in c.kwargs]
    assert len(geom_calls) == 1
    kwargs = geom_calls[0].kwargs
    assert (kwargs["x"], kwargs["y"], kwargs["width"], kwargs["height"]) == (100, 200, 800, 600)


# ---------------------------------------------------------------------------
# _cycle_below — Super+Tab rotation
# ---------------------------------------------------------------------------


def _cycle_wm() -> WindowManager:
    """WindowManager wired up so _cycle_below has a sane environment.

    Active desktop = 0; every window in the stack is treated as on-desktop,
    NORMAL, and viewable unless the test overrides those mocks. Zone
    classification is stubbed in each test via patching determine_tile_zone.
    """
    wm = _make_wm_with_dim()
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.get_window_desktop.return_value = 0
    wm.props.is_normal_window.return_value = True
    wm.props.is_viewable_window.return_value = True
    return wm


def test_cycle_below_first_press_activates_second_from_top() -> None:
    """Alt-Tab semantics: a fresh chord press (no in-flight session)
    activates the second-from-top window in the bucket — like releasing
    Alt immediately after a single Alt+Tab. Going deeper requires
    holding Super and pressing Tab again."""
    wm = _cycle_wm()
    w1, w2, w3 = MagicMock(id=0x1), MagicMock(id=0x2), MagicMock(id=0x3)
    wm.props.list_windows.return_value = [w3, w2, w1]  # bottom→top stack
    wm.props.get_active_window.return_value = w1

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()

    # Bucket is [w1 (top), w2, w3]; cursor=1 → w2 (second from top).
    wm.props.activate_window.assert_called_once_with(w2, ANY)


def test_cycle_below_held_advances_cursor_through_snapshot() -> None:
    """Held cycle: subsequent calls without an end_cycle_session() in
    between (i.e. Super still down) advance the cursor through the
    *snapshot* bucket — not the live stack. This is what makes
    "hold Super, tap Tab repeatedly" walk progressively deeper rather
    than ping-pong between the top two."""
    wm = _cycle_wm()
    w1, w2, w3 = MagicMock(id=0x1), MagicMock(id=0x2), MagicMock(id=0x3)
    wm.props.list_windows.return_value = [w3, w2, w1]
    wm.props.get_active_window.return_value = w1

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()  # cursor=1 → w2
        # Simulate the live stack reordering after the activate (w2 to top).
        # _cycle_below MUST ignore this and walk the original snapshot.
        wm.props.list_windows.return_value = [w3, w1, w2]
        wm._cycle_below()  # cursor=2 → w3 (genuinely deeper, not w1)

    targets = [c.args[0] for c in wm.props.activate_window.call_args_list]
    assert targets == [w2, w3]


def test_cycle_below_held_cursor_wraps_through_original_top() -> None:
    """Cursor wrap: after the deepest member, holding Super and tapping
    again wraps to cursor=0 (the original-top, w1) — by this point in
    the held cycle the original-top isn't visually on top any more
    (the previous taps promoted deeper windows past it), so wrapping
    through it is a real visual change and gives a full N-cycle."""
    wm = _cycle_wm()
    w1, w2, w3 = MagicMock(id=0x1), MagicMock(id=0x2), MagicMock(id=0x3)
    wm.props.list_windows.return_value = [w3, w2, w1]
    wm.props.get_active_window.return_value = w1

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()  # cursor=1 → w2
        wm._cycle_below()  # cursor=2 → w3
        wm._cycle_below()  # cursor=(2+1)%3=0 → w1

    targets = [c.args[0] for c in wm.props.activate_window.call_args_list]
    assert targets == [w2, w3, w1]


def test_cycle_below_release_resets_session_to_top_two_toggle() -> None:
    """Release-press behaviour: fully releasing Super (which calls
    end_cycle_session) makes the next press start fresh, and "fresh"
    always activates the second-from-top of the *current* stack —
    so release-press-release-press just toggles between the two
    front-most windows even with N>2 windows in the bucket."""
    wm = _cycle_wm()
    w1, w2, w3 = MagicMock(id=0x1), MagicMock(id=0x2), MagicMock(id=0x3)
    wm.props.list_windows.return_value = [w3, w2, w1]
    wm.props.get_active_window.return_value = w1

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()  # → w2

        # Simulate Super release + the resulting stack reorder.
        wm.end_cycle_session()
        wm.props.list_windows.return_value = [w3, w1, w2]
        wm.props.get_active_window.return_value = w2

        wm._cycle_below()  # fresh session over [w2, w1, w3] → cursor=1 → w1

        wm.end_cycle_session()
        wm.props.list_windows.return_value = [w3, w2, w1]
        wm.props.get_active_window.return_value = w1

        wm._cycle_below()  # fresh session again → cursor=1 → w2

    targets = [c.args[0] for c in wm.props.activate_window.call_args_list]
    assert targets == [w2, w1, w2]


def test_end_cycle_session_clears_state() -> None:
    wm = _cycle_wm()
    w1, w2 = MagicMock(id=0x1), MagicMock(id=0x2)
    wm.props.list_windows.return_value = [w2, w1]
    wm.props.get_active_window.return_value = w1

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()
    assert wm._cycle_session is not None

    wm.end_cycle_session()
    assert wm._cycle_session is None
    assert wm._cycle_cursor == 0


def test_execute_action_non_cycle_ends_cycle_session(wm: WindowManager) -> None:
    """Pressing Super+<anything-but-Tab> while a cycle session is in
    flight ends it. The user has clearly moved on — a subsequent
    Super+Tab should restart from second-from-top, not continue the
    earlier deeper-walk."""
    from windowcharmer.config.actions import TileAction

    wm._cycle_session = [MagicMock(id=0x1), MagicMock(id=0x2)]
    wm._cycle_cursor = 1
    wm.props.get_active_window.return_value = MagicMock()
    wm.props.get_workarea.return_value = (0, 40, 1920, 1000)
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = None
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False
    wm.props.list_windows.return_value = []

    wm.execute_action(TileAction.LEFT)

    assert wm._cycle_session is None


def test_execute_action_cycle_preserves_session(wm: WindowManager) -> None:
    """Inverse of the above: CYCLE itself must NOT clear the session.
    The held-cycle deeper-walk depends on the snapshot persisting across
    consecutive chord presses."""
    from windowcharmer.config.actions import TileAction

    pre_session = [MagicMock(id=0x1), MagicMock(id=0x2)]
    wm._cycle_session = pre_session
    wm._cycle_cursor = 1
    wm.props.get_workarea.return_value = (0, 40, 1920, 1000)

    with patch.object(wm, "_cycle_below"):
        wm.execute_action(TileAction.CYCLE, timestamp=12345)

    assert wm._cycle_session is pre_session
    assert wm._cycle_cursor == 1


def test_cycle_below_skips_different_zone_to_reach_same_zone() -> None:
    """A RIGHT-tiled window between the LEFT-tiled active and a LEFT-tiled
    candidate further down is skipped — different zone, different bucket."""
    wm = _cycle_wm()
    same_zone_bot = MagicMock(id=0xB07)
    different_zone = MagicMock(id=0xD1F)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [same_zone_bot, different_zone, active]
    wm.props.get_active_window.return_value = active

    def zone_for(win: object, *_: object, **__: object) -> str:
        return "right" if win is different_zone else "left"

    with patch("windowcharmer.tiling.manager.determine_tile_zone", side_effect=zone_for):
        wm._cycle_below()

    wm.props.activate_window.assert_called_once_with(same_zone_bot, ANY)


def test_cycle_below_does_not_pull_other_zone_when_no_same_zone_below() -> None:
    """Active is LEFT-tiled; only RIGHT-tiled windows sit below. The cycle
    must not yank an unrelated tile forward — it stays a no-op."""
    wm = _cycle_wm()
    right_tiled = MagicMock(id=0x71)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [right_tiled, active]
    wm.props.get_active_window.return_value = active

    def zone_for(win: object, *_: object, **__: object) -> str:
        return "right" if win is right_tiled else "left"

    with patch("windowcharmer.tiling.manager.determine_tile_zone", side_effect=zone_for):
        wm._cycle_below()

    wm.props.activate_window.assert_not_called()


def test_cycle_below_floating_active_cycles_other_floating_windows() -> None:
    """When the focused window is floating (zone contains 'unknown'), the
    cycle pulls another floating window forward — even if it's a different
    'unknown'-shaped string (e.g. 'top-unknown' vs 'unknown'). Tiled
    windows in the stack are skipped."""
    wm = _cycle_wm()
    floating_bot = MagicMock(id=0xF1)
    tiled_mid = MagicMock(id=0x71)
    floating_active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [floating_bot, tiled_mid, floating_active]
    wm.props.get_active_window.return_value = floating_active

    def zone_for(win: object, *_: object, **__: object) -> str:
        if win is floating_active:
            return "unknown"
        if win is tiled_mid:
            return "left"
        return "top-unknown"  # different unknown shape — same bucket

    with patch("windowcharmer.tiling.manager.determine_tile_zone", side_effect=zone_for):
        wm._cycle_below()

    wm.props.activate_window.assert_called_once_with(floating_bot, ANY)


def test_cycle_below_skips_window_on_other_desktop() -> None:
    """Cycling stays on the active desktop. A same-zone window on a
    different desktop is invisible to Super+Tab."""
    wm = _cycle_wm()
    other_desk = MagicMock(id=0xD1)
    same_desk = MagicMock(id=0xD0)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [other_desk, same_desk, active]
    wm.props.get_active_window.return_value = active

    def desk_for(win: object) -> int:
        return 99 if win is other_desk else 0

    wm.props.get_window_desktop.side_effect = desk_for

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()

    wm.props.activate_window.assert_called_once_with(same_desk, ANY)


def test_cycle_below_includes_sticky_window() -> None:
    """A sticky window (_NET_WM_DESKTOP == ALL_DESKTOPS) in the same zone
    participates in the cycle on every desktop."""
    from windowcharmer.x11.ewmh_client import ALL_DESKTOPS

    wm = _cycle_wm()
    wm.config.active_desktop = 1
    sticky = MagicMock(id=0x57)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [sticky, active]
    wm.props.get_active_window.return_value = active
    wm.props.get_window_desktop.return_value = ALL_DESKTOPS

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()

    wm.props.activate_window.assert_called_once_with(sticky, ANY)


def test_cycle_below_no_active_window_is_noop() -> None:
    wm = _cycle_wm()
    wm.props.get_active_window.return_value = None

    wm._cycle_below()

    wm.props.activate_window.assert_not_called()
    wm.props.list_windows.assert_not_called()


def test_cycle_below_includes_same_zone_window_with_different_geometry() -> None:
    """Regression: a too-tight 64 px geometry filter excluded a calculator
    app whose decoration was 84 px taller than the terminals tiled to
    the same zone — the user couldn't cycle into / out of the calc.
    Different apps in the same tile zone routinely have different frame
    extents; ``classify_zone``'s 128 px deviation tolerance is the right
    granularity for cycle membership, not a tighter geometry match."""
    from unittest.mock import MagicMock

    wm = _cycle_wm()
    terminal = MagicMock(id=0x46884E2)
    calc = MagicMock(id=0x6200008)
    active = MagicMock(id=0x4698C7A)
    wm.props.list_windows.return_value = [calc, terminal, active]
    wm.props.get_active_window.return_value = active

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="right"):
        wm._cycle_below()

    # All three windows are right-zone bucket members. Bucket top→bottom
    # is [active, terminal, calc]; the alt-tab first press lands on
    # cursor=1 → terminal. Held tap would advance to calc.
    wm.props.activate_window.assert_called_once_with(terminal, ANY)


def test_cycle_below_excludes_iconified_window() -> None:
    """A minimized window is still in _NET_CLIENT_LIST_STACKING but
    map_state == IsUnmapped. Activating it raises in the stack but
    leaves the window invisible — looks like a dead Super+Tab."""
    from unittest.mock import MagicMock

    wm = _cycle_wm()
    iconified = MagicMock(id=0x71)
    real = MagicMock(id=0x42)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [iconified, real, active]
    wm.props.get_active_window.return_value = active

    def viewable(win: object) -> bool:
        return win is not iconified

    wm.props.is_viewable_window.side_effect = viewable

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="right"):
        wm._cycle_below()

    targets = [c.args[0] for c in wm.props.activate_window.call_args_list]
    assert real in targets
    assert iconified not in targets


def test_cycle_below_skips_non_normal_windows() -> None:
    """Popups, dialogs, notifications etc. with geometries that happen
    to overlap a tile zone (within the 128 px tolerance) must not be
    cycled — only NORMAL windows the user manages."""
    wm = _cycle_wm()
    real = MagicMock(id=0xACE)
    popup = MagicMock(id=0x90D)  # same zone, not NORMAL
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [real, popup, active]
    wm.props.get_active_window.return_value = active

    def normal_for(win: object) -> bool:
        return win is not popup

    wm.props.is_normal_window.side_effect = normal_for

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="right"):
        wm._cycle_below()

    wm.animator.activate.return_value = False  # ensure fallback path is exercised
    # The popup must be filtered out, leaving only `real` as a candidate.
    # Either animator.activate or props.activate_window must have been
    # called with `real` — never with `popup`.
    targets_via_animator = [c.args[0] for c in wm.animator.activate.call_args_list]
    targets_via_x11 = [c.args[0] for c in wm.props.activate_window.call_args_list]
    assert popup.id not in targets_via_animator
    assert popup not in targets_via_x11
    assert real.id in targets_via_animator or real in targets_via_x11


def test_cycle_below_prefers_animator_activate_over_x11() -> None:
    """The cycle target is raised via Mutter's mw.activate (through the
    Cinnamon animator) first — Muffin filters the X11 _NET_ACTIVE_WINDOW
    path through focus-stealing prevention and silently drops it."""
    wm = _cycle_wm()
    target = MagicMock(id=0x123)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [target, active]
    wm.props.get_active_window.return_value = active
    wm.animator.activate.return_value = True  # animator handles it

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="right"):
        wm._cycle_below()

    wm.animator.activate.assert_called_once_with(target.id)
    wm.props.activate_window.assert_not_called()


def test_cycle_below_falls_back_to_x11_when_animator_misses() -> None:
    """If Cinnamon can't find the actor (or isn't running), fall back
    to the X11 EWMH path — better some raise than none."""
    wm = _cycle_wm()
    target = MagicMock(id=0x123)
    active = MagicMock(id=0xAC7)
    wm.props.list_windows.return_value = [target, active]
    wm.props.get_active_window.return_value = active
    wm.animator.activate.return_value = False  # animator unavailable / actor missing

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="right"):
        wm._cycle_below()

    wm.animator.activate.assert_called_once_with(target.id)
    wm.props.activate_window.assert_called_once_with(target, ANY)


def test_cycle_below_anchors_on_stack_top_when_active_lags() -> None:
    """Regression: Muffin reorders _NET_CLIENT_LIST_STACKING after our
    activate request but lags _NET_ACTIVE_WINDOW (sometimes for several
    seconds). Anchoring on the stale active produces every-other-press
    no-ops because the stale active sits below the freshly-raised peer
    in the stack and ``stack[:active_idx]`` no longer covers it.
    Anchoring on the top-most same-zone window in the stack instead
    keeps the cycle correct under the lag."""
    wm = _cycle_wm()
    new_top = MagicMock(id=0x1701)  # raised by the previous cycle press; now visibly on top
    stale_active = MagicMock(id=0x1AC7)  # still reported by _NET_ACTIVE_WINDOW
    wm.props.list_windows.return_value = [stale_active, new_top]
    wm.props.get_active_window.return_value = stale_active

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="right"):
        wm._cycle_below()

    # Without the stack anchor we would compute active_idx=0 (stale active
    # at the bottom of its zone) and bail. With the stack anchor we pick
    # new_top as the anchor, find stale_active beneath it, and activate
    # it — flipping the pair and continuing the rotation.
    wm.props.activate_window.assert_called_once_with(stale_active, ANY)


def test_cycle_below_active_not_in_stack_is_noop() -> None:
    """A stale active window pointer (closed mid-action) must not crash."""
    wm = _cycle_wm()
    active = MagicMock(id=0xAC7)
    other = MagicMock(id=0x999)
    wm.props.list_windows.return_value = [other]
    wm.props.get_active_window.return_value = active

    with patch("windowcharmer.tiling.manager.determine_tile_zone", return_value="left"):
        wm._cycle_below()

    wm.props.activate_window.assert_not_called()


def test_execute_action_raises_active_window_after_non_animated_tile() -> None:
    """When the animator is unavailable, the explicit X11 raise must fire
    on the focused window so it lands on top of any existing same-zone
    tile — otherwise opening a new terminal and tiling it RIGHT can
    leave it buried (the user's complaint)."""
    from windowcharmer.config.actions import TileAction

    wm = _make_wm_with_dim()
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = None

    win = _stub_window(0xACE, 0, 0, 800, 600)
    wm.props.list_windows.return_value = [win]
    wm.props.get_active_window.return_value = win
    # Force the non-animated path so the raise has to come from us.
    wm.animator.animate.return_value = False
    wm.animator.activate.return_value = False

    wm.execute_action(TileAction.LEFT)

    wm.props.activate_window.assert_called_once_with(win, ANY)


def test_execute_action_skips_explicit_raise_when_animator_handles_it() -> None:
    """The animator's JS now bundles mw.activate into the same script as
    move_resize_frame — atomic from Mutter's POV. A follow-up X11 raise
    would be a redundant round trip that can race with Mutter's own
    processing of the activate."""
    from windowcharmer.config.actions import TileAction

    wm = _make_wm_with_dim()
    win = MagicMock(id=0xACE)
    wm.props.get_active_window.return_value = win
    wm.props.list_windows.return_value = [win]
    wm.animator.animate.return_value = True  # animator handled tile + raise

    wm.execute_action(TileAction.LEFT)

    wm.props.activate_window.assert_not_called()


def test_execute_action_does_not_raise_on_resize_all() -> None:
    """BIGGER/SMALLER must NOT raise any window — the user is rebalancing
    the layout, not changing focus order. Each tile keeps its existing
    stack position."""
    from windowcharmer.config.actions import TileAction

    wm = _make_wm_with_dim()
    wm.props.is_window_maximized_vertically.return_value = False
    wm.props.is_window_maximized_horizontally.return_value = False
    wm.props.is_window_fullscreen.return_value = False
    wm.props.get_gtk_frame_extents.return_value = None
    wm.props.get_net_frame_extents.return_value = None
    wm.props.get_window_desktop.return_value = 0

    win = _stub_window(0xACE, wm.dim.x_left, wm.dim.y_top, wm.dim.w_side, wm.dim.h_full)
    wm.props.list_windows.return_value = [win]

    wm.execute_action(TileAction.BIGGER)

    wm.props.activate_window.assert_not_called()


def test_execute_action_does_not_raise_on_cycle() -> None:
    """CYCLE raises a *different* window via activate_window inside
    _cycle_below — the focused window must NOT be raised in addition,
    otherwise the cycle is undone."""
    from windowcharmer.config.actions import TileAction

    wm = _make_wm_with_dim()
    win = MagicMock(id=0xACE)
    wm.props.get_active_window.return_value = win
    wm.props.list_windows.return_value = [win]

    with patch.object(wm, "_cycle_below"):
        wm.execute_action(TileAction.CYCLE)

    wm.props.activate_window.assert_not_called()


def test_execute_action_routes_cycle_to_cycle_below(wm: WindowManager) -> None:
    """CYCLE bypasses the active-window/_TILE_SPEC dispatch path entirely.
    The chord's X server timestamp is forwarded so the EWMH activate
    inside _cycle_below carries a recent user-time."""
    from windowcharmer.config.actions import TileAction

    wm.props.get_workarea.return_value = (0, 40, 1920, 1000)
    with patch.object(wm, "_cycle_below") as cycle:
        wm.execute_action(TileAction.CYCLE, timestamp=42424242)

    cycle.assert_called_once_with(42424242)
    wm.props.get_active_window.assert_not_called()
