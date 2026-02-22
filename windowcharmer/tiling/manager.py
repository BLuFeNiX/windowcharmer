from __future__ import annotations
import threading
from Xlib import X, protocol
from Xlib.display import Display
from Xlib.xobject.drawable import Window
import traceback
import logging

from ..config import Config, ScreenDimensions, TileAction
from ..x11.display_pool import DisplayPool
from ..x11.utils import AtomCache, get_property_value
from .zones import determine_tile_zone

logger = logging.getLogger(__name__)

class WindowManager:
    def __init__(self) -> None:
        """
        Initializes the WindowManager with its own X11 display connection.
        We use a separate connection to avoid threading conflicts with the global key monitor.
        """
        self._lock = threading.Lock()
        self.d: Display = DisplayPool.get_display("wm")
        self.atom = AtomCache(self.d)
        
        screen = self.d.screen()
        self.root: Window = screen.root
        self.screenWidth: int = screen.width_in_pixels
        self.screenHeight: int = screen.height_in_pixels
        
        self.active_desktop: int = 0
        self.config: Config | None = None
        self.dim: ScreenDimensions | None = None

    def _update_state(self) -> None:
        """
        Refreshes the internal representation of the screen layout and active window.
        Uses _NET_WORKAREA to respect system panels and bars.
        """
        try:
            self.active_desktop = self.get_active_desktop()
            
            # Re-read config which might have changed (e.g. ratio index)
            self.config = Config(self.screenWidth, self.active_desktop)
            
            # Get the desktop workarea [x, y, width, height]
            workarea = get_property_value(self.root, self.atom.workarea)
            if workarea:
                _, wa_y, _, wa_h = workarea[0:4]
            else:
                wa_y, wa_h = 0, self.screenHeight

            # Update dimensions used for zone calculations
            self.dim = ScreenDimensions(
                self.screenWidth, 
                wa_y,
                wa_h,
                self.config.center_width
            )
        except Exception as e:
            logger.error(f"Error updating state: {e}")
            logger.debug(traceback.format_exc())

    def execute_action(self, action: TileAction) -> None:
        """
        Thread-safe entry point for performing a tiling action.
        Grabs the X server to ensure atomic window updates.
        """
        with self._lock:
            try:
                self.d.grab_server()
                self._update_state()
                
                win = self.get_active_window()
                
                if action == TileAction.BIGGER:
                    self.resize_all_windows(1)
                elif action == TileAction.SMALLER:
                    self.resize_all_windows(-1)
                elif win:
                    method_name = f"action_{action.value.replace('-', '_')}"
                    method = getattr(self, method_name, None)
                    if method:
                        method(win)
                    else:
                        logger.warning(f"Unknown action: {action}")
                
                self.d.flush()
            except Exception as e:
                logger.error(f"Error executing action {action}: {e}")
                logger.debug(traceback.format_exc())
            finally:
                self.d.ungrab_server()
                self.d.flush()

    # --- Actions ---

    def action_left(self, window: Window) -> None:
        if self.dim:
            self.set_max_flags(window, 1, 0)
            self.move_and_resize(window, self.dim.x_left, self.dim.y_top, self.dim.w_side, self.dim.h_full)

    def action_right(self, window: Window) -> None:
        if self.dim:
            self.set_max_flags(window, 1, 0)
            self.move_and_resize(window, self.dim.x_right, self.dim.y_top, self.dim.w_side, self.dim.h_full)

    def action_center(self, window: Window) -> None:
        if self.config and self.dim and self.config.center_width > 0:
            self.set_max_flags(window, 1, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_top, self.dim.w_center, self.dim.h_full)

    def action_top_left(self, window: Window) -> None:
        if self.dim:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_left, self.dim.y_top, self.dim.w_side, self.dim.h_half)

    def action_bottom_left(self, window: Window) -> None:
        if self.dim:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_left, self.dim.y_bottom, self.dim.w_side, self.dim.h_half)

    def action_top_right(self, window: Window) -> None:
        if self.dim:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_right, self.dim.y_top, self.dim.w_side, self.dim.h_half)

    def action_bottom_right(self, window: Window) -> None:
        if self.dim:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_right, self.dim.y_bottom, self.dim.w_side, self.dim.h_half)

    def action_top_center(self, window: Window) -> None:
        if self.config and self.dim and self.config.center_width > 0:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_top, self.dim.w_center, self.dim.h_half)

    def action_bottom_center(self, window: Window) -> None:
        if self.config and self.dim and self.config.center_width > 0:
            self.set_max_flags(window, 0, 0)
            self.move_and_resize(window, self.dim.x_center, self.dim.y_bottom, self.dim.w_center, self.dim.h_half)

    def action_max(self, window: Window) -> None:
        self.set_max_flags(window, 1, 1)

    def action_restore(self, window: Window) -> None:
        self.set_max_flags(window, 0, 0)

    # --- Helpers ---

    def move_and_resize(self, window: Window, x: int, y: int, width: int, height: int) -> None:
        """
        Fits a window into a target box (x, y, width, height).
        Handles GTK Client-Side Decorations (CSD) and standard WM titlebars.
        """
        net_fe = get_property_value(window, self.atom.extents)
        gtk_fe = self.get_gtk_frame_extents(window)
        
        d_l = d_r = d_t = d_b = 0
        if net_fe:
            d_l, d_r, d_t, d_b = net_fe[0], net_fe[1], net_fe[2], net_fe[3]
            
        if gtk_fe:
            # Shift frame origin so visible area starts at (x, y)
            x -= gtk_fe['left']
            y -= gtk_fe['top']
            
            # Expand requested size to include shadows
            width += (gtk_fe['left'] + gtk_fe['right'])
            height += (gtk_fe['top'] + gtk_fe['bottom'])

        # The X11 'configure' call expects the CLIENT area size.
        client_w = width - d_l - d_r
        client_h = height - d_t - d_b
        
        if self.is_window_maximized_vertically(window):
            self.action_restore(window)

        window.configure(
            value_mask=X.CWX | X.CWY | X.CWWidth | X.CWHeight, 
            x=int(x), y=int(y), 
            width=int(max(1, client_w)), height=int(max(1, client_h))
        )

    def set_max_flags(self, window: Window, v: int = 1, h: int = 1) -> None:
        """Sets the _NET_WM_STATE for maximization."""
        data = [v, self.atom.v_max, 0, 0, 0]
        self.send_client_message(window, self.atom.state, data)
        
        data = [h, self.atom.h_max, 0, 0, 0]
        self.send_client_message(window, self.atom.state, data)

    def send_client_message(self, window: Window, atom: int, data: list[int]) -> None:
        event = protocol.event.ClientMessage(window=window, client_type=atom, data=(32, data))
        mask = (X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.root.send_event(event, event_mask=mask)

    def get_active_window(self) -> Window | None:
        val = get_property_value(self.root, self.atom.window)
        if val and len(val) > 0:
            return self.d.create_resource_object('window', val[0])
        return None

    def get_active_desktop(self) -> int:
        val = get_property_value(self.root, self.atom.current_desktop)
        return val[0] if val else 0

    def get_gtk_frame_extents(self, window: Window) -> dict[str, int] | None:
        extents = get_property_value(window, self.atom.gtk_extents)
        if extents:
            return {'left': extents[0], 'right': extents[1], 'top': extents[2], 'bottom': extents[3]}
        return None

    def is_window_maximized_vertically(self, window: Window) -> bool:
        state = get_property_value(window, self.atom.state)
        if state:
            return self.atom.v_max in state
        return False

    def list_windows(self) -> list[Window]:
        """Returns a list of all client windows in stacking order."""
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

    def get_window_desktop(self, window: Window) -> int | None:
        """Returns the desktop index for a given window."""
        desktop = get_property_value(window, self.atom.wm_desktop)
        return desktop[0] if desktop else None

    def resize_all_windows(self, step: int) -> None:
        """
        Adjusts the ratio for all windows on the current desktop.
        Triggered by bigger/smaller actions.
        """
        # Determine zones for all current windows before updating state
        window_zones = []
        for win in self.list_windows():
            if self.get_window_desktop(win) == self.active_desktop:
                window_zones.append((
                    win,
                    determine_tile_zone(
                        win,
                        self.dim,
                        self.is_window_maximized_vertically(win)
                    )
                ))

        # Update config and recalculated dimensions
        if self.config:
             self.config.next_ratio(step)
        self._update_state()
        
        # Apply new tiling based on previous zones
        for win, zone in window_zones:
            if self.config and self.config.ratio_idx == 0:
                zone = zone.replace("center", "left")
            
            method_name = f"action_{zone.replace('-', '_')}"
            method = getattr(self, method_name, None)
            if method:
                method(win)
