import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import NamedTuple

from Xlib import X, protocol
from Xlib.display import Display
from Xlib.error import BadDrawable, BadWindow, ConnectionClosedError, DisplayConnectionError
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
    # geom=None marks a flag-only action (MAX, RESTORE) that touches
    # _NET_WM_STATE without issuing a configure. For tile actions
    # (geom set) v_max/h_max are unused — we deliberately do NOT direct-write
    # _NET_WM_STATE for tiles because Muffin resyncs in response and CSD
    # apps (e.g. Brave) flicker; the geometry write alone leaves the
    # window correctly placed without disturbing the WM's focus stack.
    geom: Callable[[ScreenDimensions], tuple[int, int, int, int]] | None
    v_max: int = 0
    h_max: int = 0
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

    Center-anchored zones collapse to the left column (left vs right is
    arbitrary — they had to go somewhere); non-center zones pass through.
    """
    return _REMAP_NO_CENTER.get(zone, zone)


def _resolve_zone_actions(
    window_zones: Iterable[tuple[Window, str]],
    has_center: bool,
) -> Iterator[tuple[Window, TileAction]]:
    """Yield (win, TileAction) pairs, applying the no-center remap when has_center is False.

    Skips zones that don't correspond to a TileAction (e.g. 'top-left-center'
    — a half-height window spanning side+center, reachable via manual drag).
    """
    for win, zone in window_zones:
        if not has_center:
            zone = _remap_zone_no_center(zone)
        try:
            yield win, TileAction(zone)
        except ValueError:
            logger.debug("Skipping window in non-tileable zone %r", zone)
            continue


# fmt: off
_TILE_SPEC: dict[TileAction, _ZoneSpec] = {
    TileAction.LEFT:          _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side,               d.h_full)),
    TileAction.LEFT_CENTER:   _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side + d.w_center,  d.h_full), needs_center=True),  # noqa: E501
    TileAction.RIGHT:         _ZoneSpec(lambda d: (d.x_right,  d.y_top,    d.w_side,               d.h_full)),
    TileAction.RIGHT_CENTER:  _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center + d.w_side,  d.h_full), needs_center=True),  # noqa: E501
    TileAction.CENTER:        _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center, d.h_full), needs_center=True),
    TileAction.TOP_LEFT:      _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side,   d.h_half)),
    TileAction.BOTTOM_LEFT:   _ZoneSpec(lambda d: (d.x_left,   d.y_bottom, d.w_side,   d.h_half)),
    TileAction.TOP_RIGHT:     _ZoneSpec(lambda d: (d.x_right,  d.y_top,    d.w_side,   d.h_half)),
    TileAction.BOTTOM_RIGHT:  _ZoneSpec(lambda d: (d.x_right,  d.y_bottom, d.w_side,   d.h_half)),
    TileAction.TOP_CENTER:    _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center, d.h_half), needs_center=True),
    TileAction.BOTTOM_CENTER: _ZoneSpec(lambda d: (d.x_center, d.y_bottom, d.w_center, d.h_half), needs_center=True),
    TileAction.MAX:           _ZoneSpec(None, 1, 1),
    TileAction.RESTORE:       _ZoneSpec(None, 0, 0),
}
# fmt: on


class WindowManager:
    def __init__(self, no_animate: bool = False) -> None:
        """Initializes the WindowManager with its own X11 display connection."""
        self._warned_missing_desktop: bool = False
        self.d: Display = DisplayPool.get_display("wm")
        self.atom = AtomCache(self.d)

        self.root: Window = self.d.screen().root

        # Config starts unsized; _update_state populates wa_w from _NET_WORKAREA
        # on the first action.
        self.config: Config = Config(wa_w=0)
        self.dim: ScreenDimensions | None = None
        self.animator: CinnamonAnimator = CinnamonAnimator(disabled=no_animate)

    def _update_state(self) -> None:
        """Refresh screen layout from X server. Uses _NET_WORKAREA for panel-aware geometry."""
        screen = self.d.screen()
        active_desktop = self.get_active_desktop()

        workarea = get_property_value(self.root, self.atom.workarea)
        if workarea:
            wa_x, wa_y, wa_w, wa_h = workarea[0:4]
        else:
            wa_x, wa_y = 0, 0
            wa_w, wa_h = screen.width_in_pixels, screen.height_in_pixels

        # Workarea must reach Config before reload so center_width is sized
        # against usable area, not raw screen width.
        self.config.set_state(wa_w, active_desktop)

        self.dim = ScreenDimensions(
            wa_x,
            wa_y,
            wa_w,
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

    @contextmanager
    def _grabbed(self) -> Iterator[None]:
        """Hold an X server grab for the duration of the block."""
        self.d.grab_server()
        try:
            yield
        finally:
            self.d.ungrab_server()
            self.d.flush()

    def execute_action(self, action: TileAction) -> None:
        """Entry point for a tiling action, called from the KeyGrabber event loop on the main thread."""
        try:
            # Read state before grabbing the server to minimise the held window.
            self._update_state()

            if action in (TileAction.BIGGER, TileAction.SMALLER):
                step = 1 if action == TileAction.BIGGER else -1
                # Animated path runs outside grab_server because Cinnamon is a
                # separate X11 client and would deadlock against our grab.
                if self._try_animated_resize_all(step):
                    return
                with self._grabbed():
                    self.resize_all_windows(step)
                return

            win = self.get_active_window()
            if win is None:
                return
            if action in (TileAction.LEFT, TileAction.RIGHT):
                action = self._resolve_tile_cycle(action, win)
            if action in _TILE_SPEC and self._try_animated_tile(action, win):
                return
            with self._grabbed():
                self._apply_tile_action(action, win)
        except (ConnectionClosedError, DisplayConnectionError):
            raise  # unrecoverable; propagate so the supervisor can restart
        except Exception as e:
            logger.error("Error executing action %s: %s", action, e)
            logger.debug("", exc_info=True)

    def _try_animated_tile(self, action: TileAction, win: Window) -> bool:
        """Attempt to animate a tile via Cinnamon compositor. Returns True if handled."""
        spec = _TILE_SPEC.get(action)
        if not self.dim or spec is None or spec.geom is None:
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
        next_center_width = self.config.peek_center_width(step)
        next_dim = ScreenDimensions(self.dim.wa_x, self.dim.wa_y, self.dim.wa_w, self.dim.wa_h, next_center_width)

        targets = [
            (win.id, x, y, w, h)
            for win, x, y, w, h in self._resolve_tile_targets(window_zones, next_dim, has_center=next_center_width > 0)
        ]
        if not targets:
            return False

        if not self.animator.animate_batch(targets):
            return False

        # Commit ratio change only after successful animation.
        self.config.next_ratio(step)
        self._update_state()
        return True

    def _resolve_tile_targets(
        self, window_zones: list[tuple[Window, str]], dim: ScreenDimensions, has_center: bool
    ) -> Iterator[tuple[Window, int, int, int, int]]:
        """Map (window, zone) pairs to (window, x, y, w, h) targets for the given layout.

        Skips windows whose resolved action has no geometry (MAX/RESTORE
        can't appear from zone strings, but the type allows it) and those
        that need the center column when it's absent.
        """
        for win, action in _resolve_zone_actions(window_zones, has_center=has_center):
            spec = _TILE_SPEC.get(action)
            if not spec or spec.geom is None or (spec.needs_center and not has_center):
                continue
            x, y, w, h = spec.geom(dim)
            yield win, x, y, w, h

    def _apply_tile_action(self, action: TileAction, win: Window) -> None:
        """Dispatch a tile action using the _TILE_SPEC table."""
        spec = _TILE_SPEC.get(action)
        if spec is None:
            logger.warning("Unhandled tile action: %s", action)
            return

        if spec.geom is None:
            # Flag-only action (MAX, RESTORE).
            self.set_max_flags(win, spec.v_max, spec.h_max)
            return

        if not self.dim:
            return
        if spec.needs_center and self.config.center_width == 0:
            logger.warning("Action %s requires the center column; ignoring (center width is 0)", action)
            return

        x, y, w, h = spec.geom(self.dim)
        self.move_and_resize(win, x, y, w, h)

    # --- Helpers ---

    def move_and_resize(self, window: Window, x: int, y: int, width: int, height: int) -> None:
        """Fits a window into (x, y, width, height), accounting for GTK CSD and WM frames."""
        x, y, client_w, client_h = self._compute_client_geometry(window, x, y, width, height)

        # Maximized and fullscreen windows ignore configure() — the WM owns
        # their geometry. Clear those flags first or the configure will be
        # rejected (or briefly applied then snapped back, producing a flicker).
        if self.is_window_maximized_vertically(window) or self.is_window_maximized_horizontally(window):
            self.set_max_flags(window, 0, 0)
        if self.is_window_fullscreen(window):
            self.set_fullscreen_flag(window, on=False)

        window.configure(
            value_mask=X.CWX | X.CWY | X.CWWidth | X.CWHeight,
            x=x,
            y=y,
            width=client_w,
            height=client_h,
        )

    def _compute_client_geometry(
        self, window: Window, x: int, y: int, width: int, height: int
    ) -> tuple[int, int, int, int]:
        """Translate a target visible rect into the X11 client geometry.

        GTK CSD windows include shadows inside the X11 window — shift the origin
        out and grow the size to absorb them. WM-decorated windows have a frame
        outside the X11 client — shrink the size to leave room for the frame.
        """
        gtk_fe = self.get_gtk_frame_extents(window)
        if gtk_fe:
            x -= gtk_fe.left
            y -= gtk_fe.top
            width += gtk_fe.left + gtk_fe.right
            height += gtk_fe.top + gtk_fe.bottom

        net_fe = self.get_net_frame_extents(window)
        if net_fe:
            width -= net_fe.left + net_fe.right
            height -= net_fe.top + net_fe.bottom

        if width < 1 or height < 1:
            logger.debug("Frame-extents math underflow: requested %dx%d → clamped to 1x1", width, height)
        return x, y, max(1, width), max(1, height)

    def set_max_flags(self, window: Window, v: int = 1, h: int = 1) -> None:
        """Sets _NET_WM_STATE maximization flags."""
        self.send_client_message(window, self.atom.wm_state, (v, self.atom.v_max, 0, 0, 0))
        self.send_client_message(window, self.atom.wm_state, (h, self.atom.h_max, 0, 0, 0))

    def set_fullscreen_flag(self, window: Window, on: bool) -> None:
        """Set or clear _NET_WM_STATE_FULLSCREEN."""
        action = 1 if on else 0
        self.send_client_message(window, self.atom.wm_state, (action, self.atom.fullscreen, 0, 0, 0))

    def send_client_message(self, window: Window, atom: int, data: tuple[int, int, int, int, int]) -> None:
        """Send a _NET_WM_STATE client message to the root window."""
        event = protocol.event.ClientMessage(window=window, client_type=atom, data=(32, list(data)))
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        self.root.send_event(event, event_mask=mask)

    def get_active_window(self) -> Window | None:
        """Returns the currently focused window, or None."""
        val = get_property_value(self.root, self.atom.active_window)
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
        extents = get_property_value(window, self.atom.gtk_frame_extents)
        if extents and len(extents) >= 4:
            return FrameExtents(extents[0], extents[1], extents[2], extents[3])
        return None

    def get_net_frame_extents(self, window: Window) -> FrameExtents | None:
        """Returns WM-decorated frame extents (titlebar + borders) if present."""
        extents = get_property_value(window, self.atom.frame_extents)
        if extents and len(extents) >= 4:
            return FrameExtents(extents[0], extents[1], extents[2], extents[3])
        return None

    def _is_maximized(self, window: Window, flag: int) -> bool:
        state = get_property_value(window, self.atom.wm_state)
        return bool(state and flag in state)

    def is_window_maximized_vertically(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_MAXIMIZED_VERT is set."""
        return self._is_maximized(window, self.atom.v_max)

    def is_window_maximized_horizontally(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_MAXIMIZED_HORZ is set."""
        return self._is_maximized(window, self.atom.h_max)

    def is_window_fullscreen(self, window: Window) -> bool:
        """Returns True if _NET_WM_STATE_FULLSCREEN is set."""
        return self._is_maximized(window, self.atom.fullscreen)

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
        # Snapshot zones BEFORE committing the new ratio: zone detection
        # matches windows against their current positions, but tile geometry
        # is computed against the post-commit layout.
        window_zones = self._collect_zoned_windows()

        self.config.next_ratio(step)
        self._update_state()
        if not self.dim:
            return

        for win, x, y, w, h in self._resolve_tile_targets(
            window_zones, self.dim, has_center=self.config.center_width > 0
        ):
            self.move_and_resize(win, x, y, w, h)

    def _collect_zoned_windows(self) -> list[tuple[Window, str]]:
        """Return (window, zone) pairs for all tiled windows on the active desktop.

        Windows on other desktops, in unknown zones, or that vanish mid-scan
        are skipped. Zones may be strings that don't map to a TileAction
        (e.g. 'top-left-center'); callers must guard against that.
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
            except (BadWindow, BadDrawable) as e:
                logger.debug("Skipping window during tiled-zone scan: %s", e)
        return result
