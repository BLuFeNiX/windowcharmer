from dataclasses import dataclass, field

@dataclass
class ScreenDimensions:
    screen_width: int
    wa_y: int
    wa_h: int
    center_width: int
    measured_decorations: int = 0

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
        side_width = (self.screen_width - self.center_width) // 2
        self.side_width = side_width

        h_half = self.wa_h // 2
        self.h_half = h_half
        self.h_full = self.wa_h

        self.x_left = 0
        self.x_right = self.screen_width - side_width
        self.x_center = side_width

        self.y_top = self.wa_y
        self.y_bottom = self.wa_y + h_half

        self.w_side = side_width
        self.w_center = self.center_width
