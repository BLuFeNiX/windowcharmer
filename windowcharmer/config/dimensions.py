from dataclasses import dataclass, field

@dataclass(frozen=True)
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
        # Calculate derived values. Since the class is frozen, we use object.__setattr__
        side_width = (self.screen_width - self.center_width) // 2
        object.__setattr__(self, 'side_width', side_width)
        
        h_half = self.wa_h // 2
        object.__setattr__(self, 'h_half', h_half)
        object.__setattr__(self, 'h_full', self.wa_h)
        
        object.__setattr__(self, 'x_left', 0)
        object.__setattr__(self, 'x_right', self.screen_width - side_width)
        object.__setattr__(self, 'x_center', side_width)
        
        object.__setattr__(self, 'y_top', self.wa_y)
        object.__setattr__(self, 'y_bottom', self.wa_y + h_half)
        
        object.__setattr__(self, 'w_side', side_width)
        object.__setattr__(self, 'w_center', self.center_width)
