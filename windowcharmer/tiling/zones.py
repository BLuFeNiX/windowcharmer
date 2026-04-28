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

    return classify_zone(abs(coords.x), abs(coords.y), geom.width, geom.height, dim, is_maximized_vertically)


def classify_zone(
    x: int,
    y: int,
    w: int,
    h: int,
    dim: ScreenDimensions,
    is_maximized_vertically: bool,
) -> str:
    """Classify an (x, y, w, h) rect into a zone string. Pure function — no X calls.

    Used by determine_tile_zone after fetching geometry, and by callers that
    already have geometry in hand (e.g. _track_windows reuses the rect it
    just snapshotted to avoid a second get_geometry round-trip).
    """

    def within(val: int, target: int) -> bool:
        return target - _ZONE_DEVIATION <= val <= target + _ZONE_DEVIATION

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

    # "full" height is implicit in TileAction zone strings — LEFT, not FULL_LEFT —
    # so drop the prefix when the window spans the workarea vertically.
    if v_pos == "full":
        return h_pos
    return f"{v_pos}-{h_pos}"
