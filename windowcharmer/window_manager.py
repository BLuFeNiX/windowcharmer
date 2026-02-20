import threading
from Xlib import X, display, protocol
import time
import traceback
import logging

from .config import Config, ScreenDimensions

logger = logging.getLogger(__name__)

# Atom cache class to avoid repeated intern_atom calls
class AtomCache:
    def __init__(self, dpy):
        self.d = dpy
        self._cache = {}

    def __getattr__(self, name):
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
            'client_list': '_NET_CLIENT_LIST',
            'client_list_stacking': '_NET_CLIENT_LIST_STACKING',
            'name': '_NET_WM_NAME',
            'name_fallback': 'WM_NAME',
        }

        if name in atom_mapping:
            atom_name = atom_mapping[name]
            if atom_name not in self._cache:
                self._cache[atom_name] = self.d.intern_atom(atom_name)
            return self._cache[atom_name]
        else:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

class WindowManager:
    def __init__(self):
        # We use a separate display connection for the manager actions
        # to avoid conflicts with the daemon/listener loop if they were shared.
        # Xlib is not thread-safe by default without locking, 
        # and sharing Display objects across threads is generally discouraged or requires XInitThreads.
        # Here we follow the pattern of creating a new display for operations or using a locked one.
        
        # However, creating a new Display for every action is slow.
        # We will maintain one Display instance protected by a Lock.
        self._lock = threading.Lock()
        self.d = display.Display()
        self.atom = AtomCache(self.d)
        
        screen = self.d.screen()
        self.root = screen.root
        self.screenWidth = screen.width_in_pixels
        self.screenHeight = screen.height_in_pixels
        
        self.active_desktop = 0
        self.config = None
        self.dim = None
        self.panel_height = 64 # Default guess

    def _update_state(self):
        """Refreshes state from X server and Config."""
        try:
            self.active_desktop = self.get_active_desktop()
            
            # Re-read config which might have changed (e.g. ratio)
            # We assume config is light enough to instantiate often
            self.config = Config(self.screenWidth, self.active_desktop)
            
            active_window = self.get_active_window()
            if active_window:
                 self.maybe_measure(active_window)
            
            # Determine panel height if possible
            ph = self.get_panel_height_from_workarea()
            if ph:
                self.panel_height = ph
            
            # Update dimensions object used for calculations
            self.dim = ScreenDimensions(
                self.screenHeight, 
                self.screenWidth, 
                self.config.center_width, 
                self.config.measured_height, 
                self.config.measured_decorations, 
                self.panel_height
            )
        except Exception as e:
            logger.error(f"Error updating state: {e}")
            logger.debug(traceback.format_exc())

    def execute_action(self, action_name):
        """
        Thread-safe entry point for performing actions.
        """
        with self._lock:
            try:
                # Sync initially to ensure we are up to date?
                # self.d.sync()
                
                # Grab server to ensure atomic updates for the user action
                # This prevents windows moving while we calculate
                self.d.grab_server()
                
                self._update_state()
                
                win = self.get_active_window()
                
                # Actions that don't require an active window
                if action_name == 'bigger':
                    self.resize_all_windows(1)
                elif action_name == 'smaller':
                    self.resize_all_windows(-1)
                elif win:
                    # Dispatch to specific action method
                    method_name = f"action_{action_name.replace('-', '_')}"
                    method = getattr(self, method_name, None)
                    if method:
                        method(win)
                    elif action_name == 'test':
                        logger.info(f"Test action on window {win.id}")
                    else:
                        logger.warning(f"Unknown action: {action_name}")
                
                self.d.flush()
            except Exception as e:
                logger.error(f"Error executing action {action_name}: {e}")
                logger.debug(traceback.format_exc())
            finally:
                self.d.ungrab_server()
                self.d.flush()

    # --- Actions ---

    def action_left(self, window):
        self.set_max_flags(window, 1, 0)
        self.move_and_resize(window, self.dim.x_left, self.dim.y_top, self.dim.w_side, self.dim.h_full)

    def action_right(self, window):
        self.set_max_flags(window, 1, 0)
        self.move_and_resize(window, self.dim.x_right, self.dim.y_top, self.dim.w_side, self.dim.h_full)

    def action_center(self, window):
        if self.config.center_width > 0:
            self.set_max_flags(window, 1, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_top, self.dim.w_center, self.dim.h_full)

    def action_top_left(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_left, self.dim.y_top, self.dim.w_side, self.dim.h_half)

    def action_bottom_left(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_left, self.dim.y_bottom, self.dim.w_side, self.dim.h_half)

    def action_top_right(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_right, self.dim.y_top, self.dim.w_side, self.dim.h_half)

    def action_bottom_right(self, window):
        self.set_max_flags(window, 0, 0)
        self.move_and_resize(window, self.dim.x_right, self.dim.y_bottom, self.dim.w_side, self.dim.h_half)

    def action_top_center(self, window):
        if self.config.center_width > 0:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_top, self.dim.w_center, self.dim.h_half)

    def action_bottom_center(self, window):
        if self.config.center_width > 0:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_bottom, self.dim.w_center, self.dim.h_half)

    def action_max(self, window):
        self.set_max_flags(window, 1, 1)

    def action_restore(self, window):
        self.set_max_flags(window, 0, 0)

    # --- Helpers ---

    def move_and_resize(self, window, x, y, width, height):
        # Compensate for GTK Frame Extents (shadows)
        gtk_fe = self.get_gtk_frame_extents(window)
        if gtk_fe:
            delta_w = gtk_fe['left'] + gtk_fe['right']
            delta_h = gtk_fe['top'] + gtk_fe['bottom'] + (self.dim.h_decor if self.dim else 0)
            
            # Apply adjustments
            width += delta_w
            height += delta_h
            x -= delta_w // 2
            # y might need adjustment depending on how we want to align
        
        # If maximized, we must restore first, otherwise move/resize might be ignored
        if self.is_window_maximized_vertically(window):
            self.action_restore(window)

        # Apply changes
        value_mask = X.CWX | X.CWY | X.CWWidth | X.CWHeight
        window.configure(value_mask=value_mask, x=x, y=y, width=width, height=height)

    def set_max_flags(self, window, v=1, h=1):
        """Sets the _NET_WM_STATE for maximization."""
        # 0: _NET_WM_STATE_REMOVE
        # 1: _NET_WM_STATE_ADD
        # 2: _NET_WM_STATE_TOGGLE
        
        # We send a client message to the root window to ask the WM to change state
        # This is the standard EWMH way.
        
        data = [v, self.atom.v_max, 0, 0, 0]
        self.send_client_message(window, self.atom.state, data)
        
        data = [h, self.atom.h_max, 0, 0, 0]
        self.send_client_message(window, self.atom.state, data)

    def send_client_message(self, window, atom, data):
        event = protocol.event.ClientMessage(window=window, client_type=atom, data=(32, data))
        mask = (X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.root.send_event(event, event_mask=mask)

    def get_active_window(self):
        prop = self.root.get_full_property(self.atom.window, X.AnyPropertyType)
        if prop and len(prop.value) > 0:
            return self.d.create_resource_object('window', prop.value[0])
        return None

    def get_active_desktop(self):
        prop = self.root.get_full_property(self.atom.current_desktop, X.AnyPropertyType)
        return prop.value[0] if prop else 0

    def get_gtk_frame_extents(self, window):        
        frame_extents = window.get_full_property(self.atom.gtk_extents, X.AnyPropertyType)
        if frame_extents:
            extents = frame_extents.value
            return {'left': extents[0], 'right': extents[1], 'top': extents[2], 'bottom': extents[3]}
        return None

    def is_window_maximized_vertically(self, window):
        state = window.get_full_property(self.atom.state, X.AnyPropertyType)        
        if state:
            return self.atom.v_max in state.value
        return False

    def maybe_measure(self, window):
        # If we have a maximized window, we can learn about screen/decor dimensions
        if self.is_window_maximized_vertically(window):
            # Only measure if we don't have GTK extents, or to confirm height
            # (Original logic)
            if not self.get_gtk_frame_extents(window):
                h, d = self.measure_window(window)
                if h != self.config.measured_height:
                    self.config.put('measured_height', h)
                if d != self.config.measured_decorations:
                    self.config.put('measured_decorations', d)

    def measure_window(self, window):
        geom = window.get_geometry()
        undecorated_height = geom.height
        
        frame_extents = window.get_full_property(self.atom.extents, X.AnyPropertyType)
        decoration_height = 0
        if frame_extents:
            decoration_height = frame_extents.value[2] + frame_extents.value[3] # top + bottom

        return geom.height, decoration_height

    def get_panel_height_from_workarea(self):
        workarea = self.root.get_full_property(self.atom.workarea, X.AnyPropertyType)
        if workarea is not None:
            # workarea is typically [x, y, width, height]
            # Assumes one panel at top/bottom
            workarea_height = workarea.value[3]
            return self.screenHeight - workarea_height
        return None

    def list_windows(self):
        window_ids = self.root.get_full_property(self.atom.client_list_stacking, X.AnyPropertyType)        
        if window_ids is None:
            window_ids = self.root.get_full_property(self.atom.client_list, X.AnyPropertyType)
        
        windows = []
        if window_ids:
            for wid in window_ids.value:
                try:
                    windows.append(self.d.create_resource_object('window', wid))
                except Exception:
                    pass
        return windows

    def get_window_desktop(self, window):
        desktop = window.get_full_property(self.atom.wm_desktop, X.AnyPropertyType)
        return desktop.value[0] if desktop else None

    def resize_all_windows(self, step):
        # Determine zones for all current windows before updating state
        window_zones = []
        for win in self.list_windows():
            if self.get_window_desktop(win) == self.active_desktop:
                window_zones.append((win, self.determine_tile_zone(win)))

        # Update config and recalculated dimensions
        self.config.next_ratio(step)
        self._update_state()
        
        # Apply new tiling based on previous zones
        for win, zone in window_zones:
            if self.config.ratio_idx == 0:
                zone = zone.replace("center", "left")
            
            method_name = f"action_{zone.replace('-', '_')}"
            method = getattr(self, method_name, None)
            if method:
                method(win)

    def determine_tile_zone(self, window, deviation=128):
        # Heuristic to guess zone based on current position/size
        x, y = self.get_window_position(window)
        geom = window.get_geometry()
        w, h = geom.width, geom.height

        def within(val, target, dev=deviation):
            return target - dev <= val <= target + dev

        v_pos = 'unknown'
        if self.is_window_maximized_vertically(window) or within(h, self.dim.h_full):
            v_pos = 'full'
        elif within(h, self.dim.h_half):
            if within(y, self.dim.y_top): v_pos = 'top'
            elif within(y, self.dim.y_bottom): v_pos = 'bottom'

        h_pos = 'unknown'
        # Check side width
        if within(w, self.dim.w_side):
            if within(x, self.dim.x_left): h_pos = 'left'
            elif within(x, self.dim.x_right): h_pos = 'right'
            elif within(x, self.dim.x_center): h_pos = 'center'
        # Check center width
        elif within(w, self.dim.w_center) and within(x, self.dim.x_center):
            h_pos = 'center'

        return f"{v_pos}-{h_pos}".replace("full-", "")

    def get_window_position(self, window):
        coords = window.translate_coords(self.root, 0, 0)
        return (abs(coords.x), abs(coords.y)) if coords else (0, 0)

