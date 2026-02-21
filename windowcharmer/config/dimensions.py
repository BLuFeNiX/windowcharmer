class ScreenDimensions:
    def __init__(self, screen_width, wa_y, wa_h, center_width, measured_decorations=0):
        # Workarea dimensions
        self.wa_y = wa_y
        self.wa_h = wa_h

        # Side column calculation
        self.side_width = (screen_width - center_width) // 2

        # Target slot heights
        self.h_half = wa_h // 2
        self.h_full = wa_h

        # Geometry zones
        self.x_left = 0
        self.x_right = screen_width - self.side_width
        self.x_center = self.side_width

        self.y_top = wa_y
        self.y_bottom = wa_y + self.h_half

        self.w_side = self.side_width
        self.w_center = center_width
