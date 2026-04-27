from Xlib.error import BadDrawable, BadWindow
from Xlib.xobject.drawable import Window

from ..config.dimensions import ScreenDimensions

# Tolerate up to this many pixels of position/size drift when matching a zone.
# 128 px covers GTK shadow offsets and minor rounding across WMs.
_ZONE_DEVIATION = 128


def determine_tile_zone(
    window: Window,
    dim: ScreenDimensions | None,
    is_maximized_vertically: bool,
    deviation: int = _ZONE_DEVIATION,
) -> str:
    """Heuristically determine which tiling zone a window currently occupies.

    Returns a zone string like ``"left"`` or ``"top-right"``.
    Returns a string containing ``"unknown"`` (e.g. ``"unknown"``,
    ``"top-unknown"``) when the window is destroyed or does not match
    any tiling zone.
    See docs/x11_coordinates.md for coordinate system notes — including
    why abs() is the correct way to recover screen coordinates from
    translate_coords' inverted result.
    """
    if not dim:
        return "unknown"

    # One get_geometry round-trip serves both the root reference for
    # translate_coords and the width/height we need below.
    try:
        geom = window.get_geometry()
        coords = window.translate_coords(geom.root, 0, 0)
    except (BadWindow, BadDrawable):
        return "unknown"
    if not coords:
        return "unknown"

    x, y = abs(coords.x), abs(coords.y)
    w, h = geom.width, geom.height

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
    elif within(w, dim.w_side + dim.w_center):
        if within(x, dim.x_left):
            h_pos = "left-center"
        elif within(x, dim.x_center):
            h_pos = "right-center"

    return f"{v_pos}-{h_pos}".removeprefix("full-")
