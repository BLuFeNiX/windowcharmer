import argparse
import json
from Xlib import X, display, Xatom, protocol
import shelve

class ScreenDimensions:
    def __init__(self, screen_height, screen_width, center_width, measured_height=None, measured_decorations=0):
        if measured_height is not None:
            screen_height = measured_height

        side_width = (screen_width - center_width) // 2
        row_height = (screen_height - measured_decorations) // 2

        self.x_left = 0
        self.x_right = screen_width - side_width
        self.x_center = (screen_width - center_width) // 2
        self.y_top = 0
        self.y_bottom = screen_height
        self.w_side = side_width
        self.w_center = center_width
        self.h_half = row_height
        self.h_full = screen_height

        self.h_decor = measured_decorations

# TODO locking
class Config:
    def __init__(self, screen_width, active_desktop, config_file='/dev/shm/tilew_state.v2.shelf'):
        self.screen_width = screen_width
        self.active_desktop = active_desktop
        self.config_file = config_file
        self.supported_ratios = [
            0,       # only 2 columns
            (3/9),   # 3 even columns
            (4/10),  # 40% center
            (5/10),  # 50% center
            (6/10),  # 60% center
        ]
        self.reload()

    def put(self, k, v):
        with shelve.open(self.config_file) as config:
            config[k] = v

    def reload(self):
        with shelve.open(self.config_file) as config:
            self.measured_height = config.get('measured_height', None)
            self.measured_decorations = config.get('measured_decorations', 0)
            self.ratio_idx = config.get(f'ratio_idx_{self.active_desktop}', 2)
            self.ratio = self.supported_ratios[self.ratio_idx]
            self.center_width = int(self.screen_width * self.ratio)
            # self.WIDTH_CHOICE = config.get(str(self.active_desktop), 1)  # default to 3 equal columns

    def next_ratio(self, step=1):
        self.ratio_idx = (self.ratio_idx + len(self.supported_ratios) + step) % len(self.supported_ratios)
        self.put(f'ratio_idx_{self.active_desktop}', self.ratio_idx)
        self.reload()

class AtomCache:
    def __init__(self, display):
        self.d = display
        self._cache = {}

    def __getattr__(self, name):
        # Custom atom names mapped to actual X11 atom names
        atom_mapping = {
            'state': '_NET_WM_STATE',
            'v_max': '_NET_WM_STATE_MAXIMIZED_VERT',
            'h_max': '_NET_WM_STATE_MAXIMIZED_HORZ',
            'desktop': '_NET_CURRENT_DESKTOP',
            'window': '_NET_ACTIVE_WINDOW',
            'extents': '_NET_FRAME_EXTENTS',
            'gtk_extents': '_GTK_FRAME_EXTENTS',
        }

        if name in atom_mapping:
            atom_name = atom_mapping[name]
            # Fetch and cache the atom if it's not already cached
            if atom_name not in self._cache:
                self._cache[atom_name] = self.d.intern_atom(atom_name)
            return self._cache[atom_name]
        else:
            # Fallback if the name is not in our custom mapping
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

class WindowManager:
    def __init__(self):
        self.d = display.Display()
        self.atom = AtomCache(self.d)
        screen = self.d.screen()
        self.root = screen.root
        self.screenWidth = screen.width_in_pixels
        self.screenHeight = screen.height_in_pixels
        self.active_desktop = self.get_active_desktop()
        self.active_window = self.get_active_window()
        self.config = Config(self.screenWidth, self.active_desktop)
        self.maybe_measure(self.active_window)
        self.dim = ScreenDimensions(self.screenHeight, self.screenWidth, self.config.center_width, self.config.measured_height, self.config.measured_decorations)

    def maybe_measure(self, window):
        if self.is_window_maximized_vertically(window):
            if not self.get_gtk_frame_extents(window):
                h, d = self.measure_window(window)
                if h != self.config.measured_height:
                    self.config.put('measured_height', h)
                if d != self.config.measured_decorations:
                    self.config.put('measured_decorations', d)

    def measure_window(self, window):
        # Get the window geometry without decorations
        geom = window.get_geometry()
        undecorated_height = geom.height

        # Try to get frame extents (decorations)
        frame_extents = window.get_full_property(self.atom.extents, X.AnyPropertyType)
        if frame_extents:
            _, _, top, bottom = frame_extents.value
            # The decoration height is the sum of the top and bottom parts
            decoration_height = top + bottom
        else:
            # If unable to get frame extents, assume no decorations
            decoration_height = 0

        return geom.height, decoration_height

    def get_active_desktop(self):
        property = self.root.get_full_property(self.atom.desktop, X.AnyPropertyType)
        return property.value[0] if property else 0

    def get_active_window(self):
        window_id = self.root.get_full_property(self.atom.window, X.AnyPropertyType).value[0]
        return self.d.create_resource_object('window', window_id)

    def move_and_resize(self, window, x, y, width, height):
        # check if the window has GTK Frame Extents
        #  this is providing some hints about how much space around the window is actually not part of the window content
        #  ex: used for drop shadows
        gtk_fe = self.get_gtk_frame_extents(window)
        if gtk_fe:
            # grow the dimensions by the amount of extra padding
            # and be sure to add the decor height
            # TODO can we measure differently to simplify this?
            delta_w = gtk_fe['left'] + gtk_fe['right']
            delta_h = gtk_fe['top'] + gtk_fe['bottom'] + self.dim.h_decor
            width += delta_w
            height += delta_h
            x -= delta_w // 2
            y -= delta_h // 2

        # setup mask of changed values - this should be the same for all invocations
        #  unless we add features like "always on top", or stop specifying certain dimensions in the caller
        value_mask = 0
        values = []
        if x is not None:
            value_mask |= X.CWX  # Window's X position
            values.append(x)
        if y is not None:
            value_mask |= X.CWY  # Window's Y position
            values.append(y)
        if width is not None and width > 0:
            value_mask |= X.CWWidth  # Window's width
            values.append(width)
        if height is not None and height > 0:
            value_mask |= X.CWHeight  # Window's height
            values.append(height)

        # Configure the window based on the specified mask and values
        window.configure(value_mask=value_mask, x=x, y=y, width=width, height=height)
        self.d.flush()

    def set_max_flags(self, window, v=1, h=1):
        data = [v, self.atom.v_max, 0, 0, 0]
        self.send_client_message(window, self.atom.state, data)
        data = [h, self.atom.h_max, 0, 0, 0]
        self.send_client_message(window, self.atom.state, data)

    def send_client_message(self, window, atom, data):
        event = protocol.event.ClientMessage(window=window, client_type=atom, data=(32, data))
        mask = (X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.root.send_event(event, event_mask=mask)
        # self.d.sync()

    def flush(self):
        self.d.flush()

    def left(self, window):
        self.set_max_flags(window, 1, 0)
        self.move_and_resize(window, self.dim.x_left, self.dim.y_top, self.dim.w_side, self.dim.h_full)

    def right(self, window):
        self.set_max_flags(window, 1, 0)
        self.move_and_resize(window, self.dim.x_right, self.dim.y_top, self.dim.w_side, self.dim.h_full)

    def top_left(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_left, self.dim.y_top, self.dim.w_side, self.dim.h_half)

    def bottom_left(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_left, self.dim.y_bottom, self.dim.w_side, self.dim.h_half)

    def top_right(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_right, self.dim.y_top, self.dim.w_side, self.dim.h_half)

    def bottom_right(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_right, self.dim.y_bottom, self.dim.w_side, self.dim.h_half)

    def center(self, window):
        if self.config.center_width > 0:
            self.set_max_flags(window, 1, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_top, self.dim.w_center, self.dim.h_full)

    def top_center(self, window):
        if self.config.center_width > 0:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_top, self.dim.w_center, self.dim.h_half)

    def bottom_center(self, window):
        if self.config.center_width > 0:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_bottom, self.dim.w_center, self.dim.h_half)

    def max(self, window, v=1, h=1):
        self.set_max_flags(window, 1, 1)

    def restore(self, window):
        self.set_max_flags(window, 0, 0)

    def bigger(self):
        self.config.next_ratio()

    def smaller(self):
        self.config.next_ratio(-1)

    def is_window_maximized_vertically(self, window):
        state = window.get_full_property(self.atom.state, X.AnyPropertyType)        
        # Check if the window is maximized vertically
        if state:
            return self.atom.v_max in state.value
        return False

    def get_gtk_frame_extents(self, window):        
        # Try to get the _GTK_FRAME_EXTENTS property of the active window
        frame_extents = window.get_full_property(self.atom.gtk_extents, X.AnyPropertyType)
        if frame_extents:
            # The property value is an array of 4 integers: [left, right, top, bottom]
            extents = frame_extents.value
            return {
                'left': extents[0],
                'right': extents[1],
                'top': extents[2],
                'bottom': extents[3]
            }
        else:
            return None

    def test(self):
        pass

def main():
    parser = argparse.ArgumentParser(description="Window management script")

    # Define a single argument for the action
    parser.add_argument("action", help="Action to perform", choices=[
        'left', 'center', 'right', 'top-left', 'bottom-left',
        'top-right', 'bottom-right', 'top-center', 'bottom-center',
        'max', 'restore', 'cycle', 'install', 'bigger', 'smaller', 'test'
    ])

    args = parser.parse_args()

    # Map action names to functions
    wm = WindowManager()
    win_actions = {
        'left': wm.left,
        'center': wm.center,
        'right': wm.right,
        'top-left': wm.top_left,
        'bottom-left': wm.bottom_left,
        'top-right': wm.top_right,
        'bottom-right': wm.bottom_right,
        'top-center': wm.top_center,
        'bottom-center': wm.bottom_center,
        'max': wm.max,
        'restore': wm.restore,
        # 'cycle': action_cycle,
        # 'install': action_install,
        'test': wm.test,
    }

    desk_actions = {
        'bigger': wm.bigger,
        'smaller': wm.smaller,
    }

    # Call the corresponding function based on the action argument
    if args.action in win_actions:
        win_actions[args.action](wm.active_window)
        wm.flush()
    elif args.action in desk_actions:
        desk_actions[args.action]()
    else:
        print(f"Invalid action: {args.action}")

if __name__ == "__main__":
    main()
