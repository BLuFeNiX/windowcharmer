import argparse
import json
from Xlib import X, display, Xatom, protocol
import shelve
import sys
import traceback

from .key_monitor import KeyMonitor

class ScreenDimensions:
    def __init__(self, screen_height, screen_width, center_width, measured_height=None, measured_decorations=0, panel_height=64):
        if measured_height is not None:
            screen_height = measured_height

        side_width = (screen_width - center_width) // 2
        row_height = (screen_height - measured_decorations) // 2

        self.x_left = 0
        self.x_right = screen_width - side_width
        self.x_center = (screen_width - center_width) // 2
        self.y_top = 0
        self.y_bottom = screen_height - row_height + panel_height
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
        with shelve.open(self.config_file) as config:
            config[k] = v

    def reload(self):
        with shelve.open(self.config_file) as config:
            self.measured_height = config.get('measured_height', None)
            self.measured_decorations = config.get('measured_decorations', 0)
            self.ratio_idx = config.get(f'ratio_idx_{self.active_desktop}', 2)
            self.ratio = self.supported_ratios[self.ratio_idx]
            self.center_width = int(self.screen_width * self.ratio)

    def next_ratio(self, step=1):
        self.ratio_idx = (self.ratio_idx + len(self.supported_ratios) + step) % len(self.supported_ratios)
        self.put(f'ratio_idx_{self.active_desktop}', self.ratio_idx)
        self.reload()

class AtomCache:
    def __init__(self, display):
        self.d = display
        self._cache = {}

    def __getattr__(self, name):
        # custom atom names mapped to actual X11 atom names
        atom_mapping = {
            'state': '_NET_WM_STATE',
            'v_max': '_NET_WM_STATE_MAXIMIZED_VERT',
            'h_max': '_NET_WM_STATE_MAXIMIZED_HORZ',
            'current_desktop': '_NET_CURRENT_DESKTOP',
            'wm_desktop': '_NET_WM_DESKTOP',
            'workarea': '_NET_WORKAREA',
            'window': '_NET_ACTIVE_WINDOW',
            'extents': '_NET_FRAME_EXTENTS',
            'gtk_extents': '_GTK_FRAME_EXTENTS',
            'client_list': '_NET_CLIENT_LIST',  # windows
            'client_list_stacking': '_NET_CLIENT_LIST_STACKING',  # back-to-front ordered windows
            'name': '_NET_WM_NAME',
            'name_fallback': 'WM_NAME',
        }

        if name in atom_mapping:
            atom_name = atom_mapping[name]
            # fetch and cache the atom if it's not already cached
            if atom_name not in self._cache:
                self._cache[atom_name] = self.d.intern_atom(atom_name)
            return self._cache[atom_name]
        else:
            # fallback if the name is not in our custom mapping
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

class WindowManager:
    def __init__(self):
        self.d = display.Display()
        self.atom = AtomCache(self.d)
        screen = self.d.screen()
        self.root = screen.root
        self.screenWidth = screen.width_in_pixels
        self.screenHeight = screen.height_in_pixels
        self.win_actions = {
            'left': self.left,
            'center': self.center,
            'right': self.right,
            'top-left': self.top_left,
            'bottom-left': self.bottom_left,
            'top-right': self.top_right,
            'bottom-right': self.bottom_right,
            'top-center': self.top_center,
            'bottom-center': self.bottom_center,
            'max': self.max,
            'restore': self.restore,
            # 'cycle': self.cycle,
            # 'install': self.install,
            'test': self.test,
        }
        self.desk_actions = {
            'bigger': self.bigger,
            'smaller': self.smaller,
        }

    # TODO fix this; need refactor of state
    def update(self):
        self.active_desktop = self.get_active_desktop()
        self.active_window = self.get_active_window()
        self.config = Config(self.screenWidth, self.active_desktop)
        self.maybe_measure(self.active_window)
        self.panel_height = self.get_panel_height_from_workarea()
        self.dim = self.create_dim()

    # TODO this is a bad hack to compensate for not being able to cleanly update config -> dim
    def create_dim(self):
        return ScreenDimensions(self.screenHeight, self.screenWidth, self.config.center_width, self.config.measured_height, self.config.measured_decorations, self.panel_height)

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

        # try to get frame extents (decorations, drop shadows, etc)
        frame_extents = window.get_full_property(self.atom.extents, X.AnyPropertyType)
        if frame_extents:
            _, _, top, bottom = frame_extents.value
            # sum of the top and bottom decorations
            decoration_height = top + bottom
        else:
            # assume no decorations
            decoration_height = 0

        return geom.height, decoration_height

    def get_active_desktop(self):
        property = self.root.get_full_property(self.atom.current_desktop, X.AnyPropertyType)
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
            # TODO this isn't needed after adding panel size compensation to dim.y_bottom
            #  but why? do we have a subtle math bug?
            # y -= delta_h // 2

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
        self.resize_all_windows(1)

    def smaller(self):
        self.resize_all_windows(-1)

    def resize_all_windows(self, step):
        # get window zones before we change the dimensions that will be used to detect them
        window_zones = [(win, self.determine_tile_zone(win)) for win in self.list_windows()]
        # update zone sizes
        self.config.next_ratio(step)
        self.dim = self.create_dim() # TODO refactor me

        # no center zone in ratio 0, so move it left
        #  TODO refactor this so we don't check over and over
        for win, zone in window_zones:
            if self.config.ratio_idx == 0:
                zone = zone.replace("center", "left")
            if "unknown" not in zone:
                self.win_actions[zone](win)

        self.flush()

    def is_window_maximized_vertically(self, window):
        state = window.get_full_property(self.atom.state, X.AnyPropertyType)        
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

    def list_windows(self, all_desktops=False):
        window_list = []
        # try to get a sorted window list
        window_ids = self.root.get_full_property(self.atom.client_list_stacking, X.AnyPropertyType)        
        if window_ids is None:
            # fallback to unsorted list
            window_ids = self.root.get_full_property(self.atom.client_list, X.AnyPropertyType)

        if window_ids:
            window_list = [self.d.create_resource_object('window', wid) for wid in window_ids.value]
            # filter windows by the current desktop
            if not all_desktops:
                window_list = [w for w in window_list if self.get_window_desktop(w) == self.active_desktop]
        return window_list

    def get_window_desktop(self, window):
        desktop = window.get_full_property(self.atom.wm_desktop, X.AnyPropertyType)
        if desktop:
            return desktop.value[0]
        return None

    def get_panel_height_from_workarea(self):
        d = display.Display()
        root = d.screen().root
        net_workarea = d.intern_atom('_NET_WORKAREA')
        workarea = root.get_full_property(net_workarea, X.AnyPropertyType)

        if workarea is not None:
            # Assuming the panel is at the top or bottom and not on the sides,
            # and that there's only one panel, or they have the same total height.
            workarea_height = workarea.value[3]
            screen_height = d.screen().height_in_pixels

            # Calculate panel height
            panel_height = (screen_height - workarea_height)
            return panel_height
        else:
            return None

    def get_window_position(self, window):
        root_window = self.d.screen().root
        translated_coords = window.translate_coords(root_window, 0, 0)
        if translated_coords:
            x, y = translated_coords.x, translated_coords.y
            return abs(x), abs(y)
        else:
            return None, None

    def determine_tile_zone(self, window, d_x=128, d_y=128, d_w=128, d_h=128):
        x, y = self.get_window_position(window)
        geom = window.get_geometry()
        w, h = geom.width, geom.height

        # Helper function to check if a value is within a deviation range
        def within(value, target, deviation):
            return target - deviation <= value <= target + deviation

        horizontal_pos = 'unknown'
        vertical_pos = 'unknown'
        
        # Determine vertical position, and whether the height implied we're tiled
        if self.is_window_maximized_vertically(window):
            vertical_pos = 'full'
        elif within(h, self.dim.h_half, d_h):
            if within(y, self.dim.y_top, d_y):
                vertical_pos = 'top'
            elif within(y, self.dim.y_bottom, d_y):
                vertical_pos = 'bottom'

        # Determine horizontal position, and whether the width implies we're tiled
        if within(w, self.dim.w_side, d_w):
            if within(x, self.dim.x_left, d_x):
                horizontal_pos = 'left'
            elif within(x, self.dim.x_right, d_x):
                horizontal_pos = 'right'
            elif within(x, self.dim.x_center, d_x):
                horizontal_pos = 'center'
        elif within(w, self.dim.w_center, d_w) and within(x, self.dim.x_center, d_x):
            horizontal_pos = 'center'

        # TODO this replace() is lazy
        return f"{vertical_pos}-{horizontal_pos}".replace("full-", "")

    def get_window_title(self, window):
        name = window.get_full_property(self.atom.name, 0)
        if not name:
            name = window.get_full_property(self.atom.name_fallback, 0)
        if not name:
            name = "Unknown"
        return name.value

    def print_window_positions(self):
        for window in self.list_windows():
            title = self.get_window_title(window)
            zone = self.determine_tile_zone(window)
            x, y = self.get_window_position(window)
            geom = window.get_geometry()
            w, h = geom.width, geom.height
            print(f"title='{title.decode('utf-8')}' zone={zone} pos=({x},{y}) size={w}x{h}")

    def test(self, window):
        self.print_window_positions()

def do_action(action):
    # we must instantiate this here because xlib cares about what thread we're on
    global wm
    if not 'wm' in globals():
        wm = WindowManager()
    try:
        wm.update()
        wm.d.grab_server()
        # Call the corresponding function based on the action argument
        if action in wm.win_actions:
            wm.win_actions[action](wm.get_active_window())
            wm.flush()
        elif action in wm.desk_actions:
            wm.desk_actions[action]()
        else:
            print(f"Invalid action: {action}")
    except:
        print("Unexpected error:", sys.exc_info()[0])
        traceback.print_exc()
    finally:
        wm.d.ungrab_server()
        wm.d.sync()

def daemonize():
    # modifier is always Super_L
    key_combinations = {
        'Up':          lambda: do_action("max"),           # Up
        'Down':        lambda: do_action("center"),        # Down
        'Left':        lambda: do_action("left"),          # Left
        'Right':       lambda: do_action("right"),         # Right
        'Space':       lambda: do_action("restore"),       # Spacebar

        'KP_Home':     lambda: do_action("top-left"),      # Numpad 7
        'KP_Up':       lambda: do_action("top-center"),    # Numpad 8
        'KP_Page_Up':  lambda: do_action("top-right"),     # Numpad 9
        'KP_Left':     lambda: do_action("left"),          # Numpad 4
        'KP_Begin':    lambda: do_action("center"),        # Numpad 5
        'KP_Right':    lambda: do_action("right"),         # Numpad 6
        'KP_End':      lambda: do_action("bottom-left"),   # Numpad 1
        'KP_Down':     lambda: do_action("bottom-center"), # Numpad 2
        'KP_Page_Down':lambda: do_action("bottom-right"),  # Numpad 3
        'KP_Insert':   lambda: do_action("restore"),       # Numpad 0

        'KP_Prior':    lambda: do_action("top-right"),     # Numpad 9 (alternate keyboard layout)
        'KP_Next':     lambda: do_action("bottom-right"),  # Numpad 3 (alternate keyboard layout)

        'KP_Add':      lambda: do_action("bigger"),        # Numpad +
        'KP_Subtract': lambda: do_action("smaller"),       # Numpad -
    }


    d = display.Display()
    monitor = KeyMonitor(d, key_combinations)
    monitor.start()

def main():
    parser = argparse.ArgumentParser(description="windowcharmer - a window tiler")

    # Create a mutually exclusive group
    group = parser.add_mutually_exclusive_group(required=True)

    # Add the positional argument "action" to the mutually exclusive group
    group.add_argument("action", nargs='?', help="Action to perform", choices=[
        'left', 'center', 'right', 'top-left', 'bottom-left',
        'top-right', 'bottom-right', 'top-center', 'bottom-center',
        'max', 'restore', 'cycle', 'install', 'bigger', 'smaller', 'test'
    ])

    # Add the "--daemonize" option to the mutually exclusive group
    group.add_argument("-d", "--daemonize", action="store_true", help="Run as a daemon")

    # Parse the arguments
    args = parser.parse_args()

    # Example usage
    if args.daemonize:
        print("Running as a daemon")
        daemonize()
    else:
        print(f"Performing action: {args.action}")
        do_action(args.action)

if __name__ == "__main__":
    main()
