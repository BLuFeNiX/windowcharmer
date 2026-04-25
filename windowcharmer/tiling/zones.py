import logging

from Xlib.error import BadDrawable, BadWindow
from Xlib.xobject.drawable import Window

from ..config import ScreenDimensions

logger = logging.getLogger(__name__)

# Tolerate up to this many pixels of position/size drift when matching a zone.
# 128 px covers GTK shadow offsets and minor rounding across WMs.
_ZONE_DEVIATION = 128


def get_window_position(window: Window) -> tuple[int, int] | None:
    """Return the (x, y) position of a window relative to the root, or None if gone.

    See docs/x11_coordinates.md for why abs() is applied to the translated coords.
    """
    try:
        root = window.get_geometry().root
        coords = window.translate_coords(root, 0, 0)
        return (abs(coords.x), abs(coords.y)) if coords else (0, 0)
    except (BadWindow, BadDrawable):
        return None


def determine_tile_zone(
    window: Window,
    dim: ScreenDimensions | None,
    is_maximized_vertically: bool,
    deviation: int = _ZONE_DEVIATION,
) -> str:
    """Heuristically determine which tiling zone a window currently occupies.

    Returns a zone string like ``"left"``, ``"top-right"``, or ``"unknown"``.
    Returns ``"unknown"`` if the window has been destroyed.
    See docs/x11_coordinates.md for coordinate system notes.
    """
    if not dim:
        return "unknown"

    try:
        pos = get_window_position(window)
        if pos is None:
            return "unknown"
        x, y = pos
        geom = window.get_geometry()
        w, h = geom.width, geom.height
    except (BadWindow, BadDrawable):
        return "unknown"

    def within(val: int, target: int, dev: int = deviation) -> bool:
        return target - dev <= val <= target + dev

    v_pos = "unknown"
    if is_maximized_vertically or within(h, dim.h_full):
        v_pos = "full"
    elif within(h, dim.h_half):
        if within(y, dim.y_top):
            v_pos = "top"
        elif within(y, dim.y_bottom):
            v_pos = "bottom"

    h_pos = "unknown"
    if within(w, dim.w_side):
        if within(x, dim.x_left):
            h_pos = "left"
        elif within(x, dim.x_right):
            h_pos = "right"
        elif within(x, dim.x_center):
            h_pos = "center"
    elif within(w, dim.w_center) and within(x, dim.x_center):
        h_pos = "center"

    return f"{v_pos}-{h_pos}".replace("full-", "")
