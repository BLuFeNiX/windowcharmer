from dataclasses import dataclass, field


@dataclass
class ScreenDimensions:
    """Zone geometry for a single screen, computed from _NET_WORKAREA and center-column width.

    All horizontal layout is anchored to the workarea rectangle (wa_x, wa_w),
    not the raw screen width, so panels on any edge — top, bottom, left, or
    right — are respected by tiling.
    """

    wa_x: int
    wa_y: int
    wa_w: int
    wa_h: int
    center_width: int

    # Calculated fields
    side_width: int = field(init=False)
    h_half: int = field(init=False)
    h_full: int = field(init=False)
    x_left: int = field(init=False)
    x_right: int = field(init=False)
    x_center: int = field(init=False)
    y_top: int = field(init=False)
    y_bottom: int = field(init=False)
    w_side: int = field(init=False)
    w_center: int = field(init=False)

    def __post_init__(self) -> None:
        side_width = (self.wa_w - self.center_width) // 2
        self.side_width = side_width

        h_half = self.wa_h // 2
        self.h_half = h_half
        self.h_full = self.wa_h

        self.x_left = self.wa_x
        self.x_right = self.wa_x + self.wa_w - side_width
        self.x_center = self.wa_x + side_width

        self.y_top = self.wa_y
        self.y_bottom = self.wa_y + h_half

        self.w_side = side_width
        # Absorb the leftover pixel from odd widths into the center column so
        # left|center|right tile to exactly wa_w.
        self.w_center = self.wa_w - 2 * side_width
