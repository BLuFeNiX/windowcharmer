from unittest.mock import MagicMock

from windowcharmer.config.dimensions import ScreenDimensions
from windowcharmer.tiling.zones import determine_tile_zone


def _dim() -> ScreenDimensions:
    # 5120x1440 screen with 40px top panel, 40% center (2048px)
    return ScreenDimensions(0, 40, 5120, 1400, 2048)


def _window(x: int, y: int, w: int, h: int) -> MagicMock:
    """Stub window whose coords match the inverted X11 system (zones.py applies abs())."""
    win = MagicMock()
    win.get_geometry.return_value = MagicMock(width=w, height=h, root=MagicMock())
    coords = MagicMock()
    # translate_coords returns negative values; zones.py applies abs() to get screen pos
    coords.x = -x
    coords.y = -y
    win.translate_coords.return_value = coords
    return win


def test_left_full() -> None:
    dim = _dim()
    win = _window(dim.x_left, dim.y_top, dim.w_side, dim.h_full)
    assert determine_tile_zone(win, dim, False) == "left"


def test_right_full() -> None:
    dim = _dim()
    win = _window(dim.x_right, dim.y_top, dim.w_side, dim.h_full)
    assert determine_tile_zone(win, dim, False) == "right"


def test_center_full() -> None:
    dim = _dim()
    win = _window(dim.x_center, dim.y_top, dim.w_center, dim.h_full)
    assert determine_tile_zone(win, dim, False) == "center"


def test_top_left() -> None:
    dim = _dim()
    win = _window(dim.x_left, dim.y_top, dim.w_side, dim.h_half)
    assert determine_tile_zone(win, dim, False) == "top-left"


def test_bottom_right() -> None:
    dim = _dim()
    win = _window(dim.x_right, dim.y_bottom, dim.w_side, dim.h_half)
    assert determine_tile_zone(win, dim, False) == "bottom-right"


def test_maximized_vertically_treated_as_full() -> None:
    dim = _dim()
    # Wrong height but is_maximized_vertically=True → v_pos is 'full'
    win = _window(dim.x_left, dim.y_top, dim.w_side, 999)
    assert determine_tile_zone(win, dim, True) == "left"


def test_unknown_zone_when_no_dim() -> None:
    win = _window(0, 0, 100, 100)
    assert determine_tile_zone(win, None, False) == "unknown"


def test_zone_deviation_tolerance() -> None:
    dim = _dim()
    # Window placed 50 px off the ideal left-full zone — within _ZONE_DEVIATION=128
    win = _window(dim.x_left + 50, dim.y_top, dim.w_side - 50, dim.h_full + 50)
    assert determine_tile_zone(win, dim, False) == "left"


def test_destroyed_window_returns_unknown() -> None:
    """get_geometry raising BadWindow (window destroyed mid-call) → 'unknown'."""
    from Xlib.error import BadWindow

    err = BadWindow.__new__(BadWindow)
    err._data = {"resource_id": 0, "sequence_number": 0, "major_opcode": 0, "minor_opcode": 0}

    win = MagicMock()
    win.get_geometry.side_effect = err
    assert determine_tile_zone(win, _dim(), False) == "unknown"
