import shelve
import sys

class KeyBindings:
    """Default key bindings mapping keysyms to actions."""
    
    @staticmethod
    def get_defaults():
        return {
            'Up':           'max',
            'Down':         'center',
            'Left':         'left',
            'Right':        'right',
            'space':        'restore',

            'KP_Home':      'top-left',
            'KP_Up':        'top-center',
            'KP_Page_Up':   'top-right',
            'KP_Left':      'left',
            'KP_Begin':     'center',
            'KP_Right':     'right',
            'KP_End':       'bottom-left',
            'KP_Down':      'bottom-center',
            'KP_Page_Down': 'bottom-right',
            'KP_Insert':    'restore',

            'KP_Prior':     'top-right',
            'KP_Next':      'bottom-right',

            'KP_Add':       'bigger',
            'KP_Subtract':  'smaller',
            
            'BackSpace':    'exit',
        }

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
