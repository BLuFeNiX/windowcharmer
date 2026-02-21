import shelve


class Config:
    def __init__(self, screen_width, active_desktop, config_file='/dev/shm/tilew_state.v2.shelf'):
        self.screen_width = screen_width
        self.active_desktop = active_desktop
        self.config_file = config_file
        self.supported_ratios = [
            0,        # only 2 columns
            (3/9),    # 3 even columns
            (40/100), # 40% center
            (45/100), # 45% center
            (50/100), # 50% center
            (55/100), # 55% center
            (60/100), # 60% center
            (65/100), # 65% center
        ]

        self.reload()

    def put(self, k, v):
        try:
            with shelve.open(self.config_file) as config:
                config[k] = v
        except Exception:
            pass

    def reload(self):
        try:
            with shelve.open(self.config_file) as config:
                self.measured_height = config.get('measured_height', None)
                self.measured_decorations = config.get('measured_decorations', 0)
                self.ratio_idx = config.get(f'ratio_idx_{self.active_desktop}', 2)
        except Exception:
            # Fallback defaults if file error
            self.measured_height = None
            self.measured_decorations = 0
            self.ratio_idx = 2

        self.ratio = self.supported_ratios[self.ratio_idx]
        self.center_width = int(self.screen_width * self.ratio)

    def next_ratio(self, step=1):
        self.ratio_idx = (self.ratio_idx + len(self.supported_ratios) + step) % len(self.supported_ratios)
        self.put(f'ratio_idx_{self.active_desktop}', self.ratio_idx)
        self.reload()
