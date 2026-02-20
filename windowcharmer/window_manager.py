import threading
from Xlib import X, display, protocol
import time
import traceback
import logging

from .config import Config, ScreenDimensions
from .x11_utils import AtomCache, get_property_value

logger = logging.getLogger(__name__)

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
            
            # Get workarea
            workarea = get_property_value(self.root, self.atom.workarea)
            if workarea:
                # workarea is [x, y, w, h]
                wa_x, wa_y, wa_w, wa_h = workarea[0:4]
            else:
                wa_x, wa_y, wa_w, wa_h = 0, 0, self.screenWidth, self.screenHeight

            # Update dimensions object used for calculations
            self.dim = ScreenDimensions(
                self.screenWidth, 
                wa_y,
                wa_h,
                self.config.center_width, 
                self.config.measured_decorations
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
        # We need to determine how much the Window Manager (WM) or the 
        # Application (if CSD) is going to add to our requested client size.
        
        net_fe = get_property_value(window, self.atom.extents)
        gtk_fe = self.get_gtk_frame_extents(window)
        
        # Default: no decorations
        d_l = d_r = d_t = d_b = 0
        
        # If both are present, we need to be careful.
        # Usually, if gtk_fe is present, it contains the shadows.
        # If net_fe is present, it contains the titlebar.
        
        if net_fe:
            d_l += net_fe[0]
            d_r += net_fe[1]
            d_t += net_fe[2]
            d_b += net_fe[3]
            
        if gtk_fe:
            # For GTK windows, the 'x, y' of the window frame 
            # actually includes the shadows. So to place the 
            # VISIBLE part at x, y, we must shift by the shadows.
            x -= gtk_fe['left']
            y -= gtk_fe['top']
            
            # The requested size must INCLUDE the shadows 
            # so the visible area remains the correct size.
            width += (gtk_fe['left'] + gtk_fe['right'])
            height += (gtk_fe['top'] + gtk_fe['bottom'])

        # Now, standard X11 'configure' on a managed window 
        # usually takes the CLIENT size.
        client_w = width - d_l - d_r
        client_h = height - d_t - d_b
        
        # If maximized, we must restore first
        if self.is_window_maximized_vertically(window):
            self.action_restore(window)

        # Apply changes
        value_mask = X.CWX | X.CWY | X.CWWidth | X.CWHeight
        window.configure(
            value_mask=value_mask, 
            x=int(x), 
            y=int(y), 
            width=int(max(1, client_w)), 
            height=int(max(1, client_h))
        )

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
        val = get_property_value(self.root, self.atom.window)
        if val and len(val) > 0:
            return self.d.create_resource_object('window', val[0])
        return None

    def get_active_desktop(self):
        val = get_property_value(self.root, self.atom.current_desktop)
        return val[0] if val else 0

    def get_gtk_frame_extents(self, window):        
        extents = get_property_value(window, self.atom.gtk_extents)
        if extents:
            return {'left': extents[0], 'right': extents[1], 'top': extents[2], 'bottom': extents[3]}
        return None

    def is_window_maximized_vertically(self, window):
        state = get_property_value(window, self.atom.state)
        if state:
            return self.atom.v_max in state
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
        
        frame_extents = get_property_value(window, self.atom.extents)
        decoration_height = 0
        if frame_extents:
            decoration_height = frame_extents[2] + frame_extents[3] # top + bottom

        return geom.height, decoration_height

    def get_panel_height_from_workarea(self):
        workarea = get_property_value(self.root, self.atom.workarea)
        if workarea is not None:
            # workarea is typically [x, y, width, height]
            # Assumes one panel at top/bottom
            workarea_height = workarea[3]
            return self.screenHeight - workarea_height
        return None

    def list_windows(self):
        window_ids = get_property_value(self.root, self.atom.client_list_stacking)
        if window_ids is None:
            window_ids = get_property_value(self.root, self.atom.client_list)
        
        windows = []
        if window_ids:
            for wid in window_ids:
                try:
                    windows.append(self.d.create_resource_object('window', wid))
                except Exception:
                    pass
        return windows

    def get_window_desktop(self, window):
        desktop = get_property_value(window, self.atom.wm_desktop)
        return desktop[0] if desktop else None

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

