import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple

from Xlib import X, protocol
from Xlib.display import Display
from Xlib.error import ConnectionClosedError, DisplayConnectionError
from Xlib.xobject.drawable import Window

from ..cinnamon.animator import CinnamonAnimator
from ..config.actions import TileAction
from ..config.dimensions import ScreenDimensions
from ..config.settings import Config
from ..x11.display_pool import DisplayPool
from ..x11.utils import AtomCache, get_property_value
from .zones import determine_tile_zone

logger = logging.getLogger(__name__)

# _NET_WM_DESKTOP sentinel for "show on all desktops" (sticky windows).
_ALL_DESKTOPS = 0xFFFFFFFF


@dataclass(frozen=True)
class FrameExtents:
    left: int
    right: int
    top: int
    bottom: int


class _ZoneSpec(NamedTuple):
    geom: Callable[[ScreenDimensions], tuple[int, int, int, int]]
    v_max: int
    h_max: int
    needs_center: bool = False


_REMAP_NO_CENTER: dict[str, str] = {
    "left-center": "left",
    "right-center": "right",
    "center": "left",
    "top-center": "top-left",
    "bottom-center": "bottom-left",
}


def _remap_zone_no_center(zone: str) -> str:
    """Map center-spanning zones to their side-only equivalents when center_width == 0.

    Center-anchored zones collapse to the left column; non-center zones pass through.
    """
    return _REMAP_NO_CENTER.get(zone, zone)


# fmt: off
_TILE_SPEC: dict[TileAction, _ZoneSpec] = {
    TileAction.LEFT:          _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side,               d.h_full), 1, 0),
    TileAction.LEFT_CENTER:   _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side + d.w_center,  d.h_full), 1, 0, True),  # noqa: E501
    TileAction.RIGHT:         _ZoneSpec(lambda d: (d.x_right,  d.y_top,    d.w_side,               d.h_full), 1, 0),
    TileAction.RIGHT_CENTER:  _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center + d.w_side,  d.h_full), 1, 0, True),  # noqa: E501
    TileAction.CENTER:        _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center, d.h_full), 1, 0, True),
    TileAction.TOP_LEFT:      _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side,   d.h_half), 0, 0),
    TileAction.BOTTOM_LEFT:   _ZoneSpec(lambda d: (d.x_left,   d.y_bottom, d.w_side,   d.h_half), 0, 0),
    TileAction.TOP_RIGHT:     _ZoneSpec(lambda d: (d.x_right,  d.y_top,    d.w_side,   d.h_half), 0, 0),
    TileAction.BOTTOM_RIGHT:  _ZoneSpec(lambda d: (d.x_right,  d.y_bottom, d.w_side,   d.h_half), 0, 0),
    TileAction.TOP_CENTER:    _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center, d.h_half), 0, 0, True),
    TileAction.BOTTOM_CENTER: _ZoneSpec(lambda d: (d.x_center, d.y_bottom, d.w_center, d.h_half), 0, 0, True),
}
# fmt: on


class WindowManager:
    def __init__(self, no_animate: bool = False) -> None:
        """Initializes the WindowManager with its own X11 display connection."""
        self._warned_missing_desktop: bool = False
        self.d: Display = DisplayPool.get_display("wm")
        self.atom = AtomCache(self.d)

        screen = self.d.screen()
        self.root: Window = screen.root
        self.screen_width: int = screen.width_in_pixels
        # screen_width is refreshed in _update_state to handle RandR changes.

        self.config: Config = Config(self.screen_width)
        self.dim: ScreenDimensions | None = None
        self.animator: CinnamonAnimator = CinnamonAnimator(disabled=no_animate)

    def _update_state(self) -> None:
        """Refresh screen layout from X server. Uses _NET_WORKAREA for panel-aware geometry."""
        screen = self.d.screen()
        self.screen_width = screen.width_in_pixels

        active_desktop = self.get_active_desktop()
        self.config.set_state(self.screen_width, active_desktop)

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

    def _resolve_tile_cycle(self, action: TileAction, win: Window) -> TileAction:
        """Cycle LEFT↔LEFT_CENTER and RIGHT↔RIGHT_CENTER based on the window's current zone."""
        if self.config.center_width == 0:
            return action
        zone = determine_tile_zone(win, self.dim, self.is_window_maximized_vertically(win))
        if action == TileAction.LEFT:
            if zone == "left":
                return TileAction.LEFT_CENTER
            if zone == "left-center":
                return TileAction.LEFT
        elif action == TileAction.RIGHT:
            if zone == "right":
                return TileAction.RIGHT_CENTER
            if zone == "right-center":
                return TileAction.RIGHT
        return action

    def execute_action(self, action: TileAction) -> None:
        """Entry point for a tiling action, called from the KeyGrabber event loop on the main thread."""
        try:
            # Read state before grabbing the server to minimise the held window.
            self._update_state()
            win = self.get_active_window()

            if win and action in (TileAction.LEFT, TileAction.RIGHT):
                action = self._resolve_tile_cycle(action, win)

            # Tile actions can be animated via Cinnamon's compositor. This must
            # run outside grab_server because Cinnamon is a separate X11 client.
            if action in _TILE_SPEC and win and self._try_animated_tile(action, win):
                return
            if action == TileAction.BIGGER and self._try_animated_resize_all(1):
                return
            if action == TileAction.SMALLER and self._try_animated_resize_all(-1):
                return

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

    def _try_animated_tile(self, action: TileAction, win: Window) -> bool:
        """Attempt to animate a tile via Cinnamon compositor. Returns True if handled."""
        spec = _TILE_SPEC.get(action)
        if not self.dim or spec is None:
            return False
        if spec.needs_center and self.config.center_width == 0:
            return False
        x, y, w, h = spec.geom(self.dim)
        return self.animator.animate(win.id, x, y, w, h)

    def _try_animated_resize_all(self, step: int) -> bool:
        """Animate all tiled windows to the next ratio in one compositor call."""
        if not self.dim:
            return False

        window_zones = self._collect_zoned_windows()
        if not window_zones:
            return False

        # Compute what the layout will look like after the ratio step, without
        # committing the change yet so the fallback path can do it if needed.
        next_idx = (self.config.ratio_idx + step) % len(Config.supported_ratios)
        next_center_width = int(self.screen_width * Config.supported_ratios[next_idx])
        next_dim = ScreenDimensions(self.screen_width, self.dim.wa_y, self.dim.wa_h, next_center_width)

        targets = []
        for win, zone in window_zones:
            if next_idx == 0:
                zone = _remap_zone_no_center(zone)
            try:
                action = TileAction(zone)
            except ValueError:
                continue
            spec = _TILE_SPEC.get(action)
            if not spec:
                continue
            if spec.needs_center and next_center_width == 0:
                continue
            x, y, w, h = spec.geom(next_dim)
            targets.append((win.id, x, y, w, h))

        if not targets:
            return False

        if not self.animator.animate_batch(targets):
            return False

        # Commit ratio change only after successful animation.
        self.config.next_ratio(step)
        self._update_state()
        return True

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

        x, y, w, h = spec.geom(self.dim)
        self.set_max_flags(win, spec.v_max, spec.h_max)
        self.move_and_resize(win, x, y, w, h)

    # --- Helpers ---

    def move_and_resize(self, window: Window, x: int, y: int, width: int, height: int) -> None:
        """Fits a window into (x, y, width, height), accounting for GTK CSD and WM frames."""
        net_fe = get_property_value(window, self.atom.extents)
        gtk_fe = self.get_gtk_frame_extents(window)

        d_l = d_r = d_t = d_b = 0
        if net_fe and len(net_fe) >= 4:
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
        if val and val[0]:
            return self.d.create_resource_object("window", val[0])
        return None

    def get_active_desktop(self) -> int:
        """Returns the index of the current virtual desktop."""
        val = get_property_value(self.root, self.atom.current_desktop)
        if not val:
            if not self._warned_missing_desktop:
                logger.warning("_NET_CURRENT_DESKTOP not set — WM may not be EWMH-compliant; defaulting to desktop 0")
                self._warned_missing_desktop = True
            return 0
        return val[0]

    def get_gtk_frame_extents(self, window: Window) -> FrameExtents | None:
        """Returns GTK CSD shadow extents if present."""
        extents = get_property_value(window, self.atom.gtk_extents)
        if extents and len(extents) >= 4:
            return FrameExtents(extents[0], extents[1], extents[2], extents[3])
        return None

    def _is_maximized(self, window: Window, flag: int) -> bool:
        state = get_property_value(window, self.atom.state)
        return bool(state and flag in state)

    def is_window_maximized_vertically(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_MAXIMIZED_VERT is set."""
        return self._is_maximized(window, self.atom.v_max)

    def is_window_maximized_horizontally(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_MAXIMIZED_HORZ is set."""
        return self._is_maximized(window, self.atom.h_max)

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
        window_zones = self._collect_zoned_windows()

        self.config.next_ratio(step)
        self._update_state()

        for win, zone in window_zones:
            if self.config.ratio_idx == 0:
                zone = _remap_zone_no_center(zone)
            try:
                action = TileAction(zone)
            except ValueError:
                continue
            self._apply_tile_action(action, win)

    def _collect_zoned_windows(self) -> list[tuple[Window, str]]:
        """Return (window, zone) pairs for all tiled windows on the active desktop.

        Windows on other desktops, in unknown zones, or that raise during
        inspection are skipped. Zones may be strings that don't map to a
        TileAction (e.g. 'top-left-center'); callers must guard against that.
        """
        result: list[tuple[Window, str]] = []
        for win in self.list_windows():
            try:
                desktop = self.get_window_desktop(win)
                if desktop != self.config.active_desktop and desktop != _ALL_DESKTOPS:
                    continue
                zone = determine_tile_zone(win, self.dim, self.is_window_maximized_vertically(win))
                if "unknown" not in zone:
                    result.append((win, zone))
            except Exception as e:
                logger.debug("Skipping window during tiled-zone scan: %s", e)
        return result
