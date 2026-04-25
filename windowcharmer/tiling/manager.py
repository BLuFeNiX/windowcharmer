import logging
import threading
from dataclasses import dataclass
from typing import NamedTuple

from Xlib import X, protocol
from Xlib.display import Display
from Xlib.error import ConnectionClosedError, DisplayConnectionError
from Xlib.xobject.drawable import Window

from ..config import Config, ScreenDimensions, TileAction
from ..x11.display_pool import DisplayPool
from ..x11.utils import AtomCache, get_property_value
from .zones import determine_tile_zone

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrameExtents:
    left: int
    right: int
    top: int
    bottom: int


class _ZoneSpec(NamedTuple):
    x_attr: str
    y_attr: str
    w_attr: str
    h_attr: str
    v_max: int
    h_max: int
    needs_center: bool = False


# Maps each tile action to its target zone geometry and max-flag state.
# fmt: off
_TILE_SPEC: dict[TileAction, _ZoneSpec] = {
    TileAction.LEFT:          _ZoneSpec('x_left',   'y_top',    'w_side',   'h_full', 1, 0),
    TileAction.RIGHT:         _ZoneSpec('x_right',  'y_top',    'w_side',   'h_full', 1, 0),
    TileAction.CENTER:        _ZoneSpec('x_center', 'y_top',    'w_center', 'h_full', 1, 0, True),
    TileAction.TOP_LEFT:      _ZoneSpec('x_left',   'y_top',    'w_side',   'h_half', 0, 0),
    TileAction.BOTTOM_LEFT:   _ZoneSpec('x_left',   'y_bottom', 'w_side',   'h_half', 0, 0),
    TileAction.TOP_RIGHT:     _ZoneSpec('x_right',  'y_top',    'w_side',   'h_half', 0, 0),
    TileAction.BOTTOM_RIGHT:  _ZoneSpec('x_right',  'y_bottom', 'w_side',   'h_half', 0, 0),
    TileAction.TOP_CENTER:    _ZoneSpec('x_center', 'y_top',    'w_center', 'h_half', 0, 0, True),
    TileAction.BOTTOM_CENTER: _ZoneSpec('x_center', 'y_bottom', 'w_center', 'h_half', 0, 0, True),
}
# fmt: on


class WindowManager:
    def __init__(self) -> None:
        """Initializes the WindowManager with its own X11 display connection."""
        self._lock = threading.Lock()
        self.d: Display = DisplayPool.get_display("wm")
        self.atom = AtomCache(self.d)

        screen = self.d.screen()
        self.root: Window = screen.root
        self.screen_width: int = screen.width_in_pixels
        # screen_width is refreshed in _update_state to handle RandR changes.

        self.config: Config = Config(self.screen_width)
        self.dim: ScreenDimensions | None = None

    def _update_state(self) -> None:
        """Refreshes screen layout from X server. Uses _NET_WORKAREA for panel-aware geometry."""
        try:
            screen = self.d.screen()
            self.screen_width = screen.width_in_pixels

            active_desktop = self.get_active_desktop()

            self.config.update_screen_width(self.screen_width)
            self.config.set_active_desktop(active_desktop)

            workarea = get_property_value(self.root, self.atom.workarea)
            if workarea:
                _, wa_y, _, wa_h = workarea[0:4]
            else:
                wa_y, wa_h = 0, screen.height_in_pixels

            self.dim = ScreenDimensions(
                self.screen_width,
                wa_y,
                wa_h,
                self.config.center_width,
            )
        except Exception as e:
            logger.error(f"Error updating state: {e}")
            logger.debug("", exc_info=True)
            self.dim = None  # mark stale so next action is a no-op rather than using old geometry

    def execute_action(self, action: TileAction) -> None:
        """Thread-safe entry point for a tiling action. State is read before grabbing the server."""
        with self._lock:
            try:
                # Read state before grabbing the server to minimise the held window.
                self._update_state()
                win = self.get_active_window()

                self.d.grab_server()
                try:
                    match action:
                        case TileAction.BIGGER:
                            self.resize_all_windows(1)
                        case TileAction.SMALLER:
                            self.resize_all_windows(-1)
                        case _ if win:
                            self._apply_tile_action(action, win)
                finally:
                    self.d.ungrab_server()
                    self.d.flush()
            except (ConnectionClosedError, DisplayConnectionError):
                raise  # unrecoverable; propagate so the supervisor can restart
            except Exception as e:
                logger.error(f"Error executing action {action}: {e}")
                logger.debug("", exc_info=True)

    def _apply_tile_action(self, action: TileAction, win: Window) -> None:
        """Dispatch a tile action using the _TILE_SPEC table."""
        if action == TileAction.MAX:
            self.set_max_flags(win, 1, 1)
            return
        if action == TileAction.RESTORE:
            self.set_max_flags(win, 0, 0)
            return

        spec = _TILE_SPEC.get(action)
        if spec is None:
            logger.warning(f"Unhandled tile action: {action}")
            return

        if not self.dim:
            return
        if spec.needs_center and self.config.center_width == 0:
            return

        self.set_max_flags(win, spec.v_max, spec.h_max)
        self.move_and_resize(
            win,
            getattr(self.dim, spec.x_attr),
            getattr(self.dim, spec.y_attr),
            getattr(self.dim, spec.w_attr),
            getattr(self.dim, spec.h_attr),
        )

    # --- Helpers ---

    def move_and_resize(self, window: Window, x: int, y: int, width: int, height: int) -> None:
        """Fits a window into (x, y, width, height), accounting for GTK CSD and WM frames."""
        net_fe = get_property_value(window, self.atom.extents)
        gtk_fe = self.get_gtk_frame_extents(window)

        d_l = d_r = d_t = d_b = 0
        if net_fe:
            d_l, d_r, d_t, d_b = net_fe[0], net_fe[1], net_fe[2], net_fe[3]

        if gtk_fe:
            # Shift frame origin so visible area starts at (x, y)
            x -= gtk_fe.left
            y -= gtk_fe.top
            # Expand requested size to include shadows
            width += gtk_fe.left + gtk_fe.right
            height += gtk_fe.top + gtk_fe.bottom

        # configure() expects the client area, not the decorated size.
        client_w = width - d_l - d_r
        client_h = height - d_t - d_b

        # Maximized windows ignore configure(); clear both flags first.
        if self.is_window_maximized_vertically(window) or self.is_window_maximized_horizontally(window):
            self.set_max_flags(window, 0, 0)

        window.configure(
            value_mask=X.CWX | X.CWY | X.CWWidth | X.CWHeight,
            x=int(x),
            y=int(y),
            width=int(max(1, client_w)),
            height=int(max(1, client_h)),
        )

    def set_max_flags(self, window: Window, v: int = 1, h: int = 1) -> None:
        """Sets _NET_WM_STATE maximization flags."""
        self.send_client_message(window, self.atom.state, (v, self.atom.v_max, 0, 0, 0))
        self.send_client_message(window, self.atom.state, (h, self.atom.h_max, 0, 0, 0))

    def send_client_message(self, window: Window, atom: int, data: tuple[int, int, int, int, int]) -> None:
        """Send a _NET_WM_STATE client message to the root window."""
        event = protocol.event.ClientMessage(window=window, client_type=atom, data=(32, list(data)))
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        self.root.send_event(event, event_mask=mask)

    def get_active_window(self) -> Window | None:
        """Returns the currently focused window, or None."""
        val = get_property_value(self.root, self.atom.window)
        if val:
            return self.d.create_resource_object("window", val[0])
        return None

    def get_active_desktop(self) -> int:
        """Returns the index of the current virtual desktop."""
        val = get_property_value(self.root, self.atom.current_desktop)
        return val[0] if val else 0

    def get_gtk_frame_extents(self, window: Window) -> FrameExtents | None:
        """Returns GTK CSD shadow extents if present."""
        extents = get_property_value(window, self.atom.gtk_extents)
        if extents and len(extents) >= 4:
            return FrameExtents(extents[0], extents[1], extents[2], extents[3])
        return None

    def is_window_maximized_vertically(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_MAXIMIZED_VERT is set."""
        state = get_property_value(window, self.atom.state)
        if state:
            return self.atom.v_max in state
        return False

    def is_window_maximized_horizontally(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_MAXIMIZED_HORZ is set."""
        state = get_property_value(window, self.atom.state)
        if state:
            return self.atom.h_max in state
        return False

    def list_windows(self) -> list[Window]:
        """Returns all client windows in stacking order."""
        window_ids = get_property_value(self.root, self.atom.client_list_stacking)
        if window_ids is None:
            window_ids = get_property_value(self.root, self.atom.client_list)
        if not window_ids:
            return []
        return [self.d.create_resource_object("window", wid) for wid in window_ids]

    def get_window_desktop(self, window: Window) -> int | None:
        """Returns the desktop index for a given window."""
        desktop = get_property_value(window, self.atom.wm_desktop)
        return desktop[0] if desktop else None

    def resize_all_windows(self, step: int) -> None:
        """Adjusts the center-column ratio for all tiled windows on the active desktop."""
        window_zones = []
        for win in self.list_windows():
            try:
                if self.get_window_desktop(win) != self.config.active_desktop:
                    continue
                zone = determine_tile_zone(win, self.dim, self.is_window_maximized_vertically(win))
                window_zones.append((win, zone))
            except Exception as e:
                logger.debug(f"Skipping window during resize_all: {e}")

        self.config.next_ratio(step)
        self._update_state()

        for win, zone in window_zones:
            if self.config.ratio_idx == 0:
                zone = zone.replace("center", "left")
            try:
                self._apply_tile_action(TileAction(zone), win)
            except ValueError:
                logger.warning(f"Unknown zone: {zone}")
