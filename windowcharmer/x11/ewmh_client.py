"""EWMH client — all property reads, state writes, and client messages.

Extracted from WindowManager so the tiling layer holds policy and geometry
only. The X11 vocabulary (atoms, get_full_property, send_event, configure
masks) lives behind this single collaborator.
"""

import logging
from dataclasses import dataclass

from Xlib import X, protocol
from Xlib.display import Display
from Xlib.xobject.drawable import Window

from .utils import Atoms, get_property_value

logger = logging.getLogger(__name__)

# _NET_WM_DESKTOP sentinel for "show on all desktops" (sticky windows).
ALL_DESKTOPS = 0xFFFFFFFF


@dataclass(frozen=True)
class FrameExtents:
    left: int
    right: int
    top: int
    bottom: int


class EwmhClient:
    """Encapsulates EWMH/ICCCM-flavoured window operations on a single display.

    Owns the interned Atoms, the root window handle, and a one-shot warning
    flag for non-EWMH WMs that omit _NET_CURRENT_DESKTOP. Property fetches
    go through get_property_value which swallows BadWindow/BadDrawable so
    callers never see X11 error types for vanished windows.
    """

    def __init__(self, dpy: Display) -> None:
        self._dpy = dpy
        self._atom = Atoms(dpy)
        self._root: Window = dpy.screen().root
        self._warned_missing_desktop: bool = False

    # --- Screen / workarea ---

    def get_screen_size(self) -> tuple[int, int]:
        """Returns (width, height) of the default screen in pixels."""
        screen = self._dpy.screen()
        return screen.width_in_pixels, screen.height_in_pixels

    def get_workarea(self) -> tuple[int, int, int, int] | None:
        """Returns (x, y, w, h) of _NET_WORKAREA, or None if unset.

        Bare WMs without panels often omit this property; callers must fall
        back to the full screen size.
        """
        val = get_property_value(self._root, self._atom.workarea)
        if val and len(val) >= 4:
            return val[0], val[1], val[2], val[3]
        return None

    # --- Desktop / window discovery ---

    def get_active_window(self) -> Window | None:
        """Returns the currently focused window, or None.

        _NET_ACTIVE_WINDOW = [0] means "no active window"; we do NOT return
        a Window resource for ID 0.
        """
        val = get_property_value(self._root, self._atom.active_window)
        if val and val[0]:
            return self._dpy.create_resource_object("window", val[0])
        return None

    def get_active_desktop(self) -> int:
        """Returns the index of the current virtual desktop.

        Logs a one-time warning and returns 0 on minimal WMs that omit
        _NET_CURRENT_DESKTOP — repeat-warning would spam logs on every action.
        """
        val = get_property_value(self._root, self._atom.current_desktop)
        if not val:
            if not self._warned_missing_desktop:
                logger.warning("_NET_CURRENT_DESKTOP not set — WM may not be EWMH-compliant; defaulting to desktop 0")
                self._warned_missing_desktop = True
            return 0
        return val[0]

    def list_windows(self) -> list[Window]:
        """Returns all client windows in stacking order (top-most last).

        Falls back to _NET_CLIENT_LIST (creation order) when stacking is
        absent — some minimal WMs only export the unordered list.
        """
        window_ids = get_property_value(self._root, self._atom.client_list_stacking)
        if window_ids is None:
            window_ids = get_property_value(self._root, self._atom.client_list)
        if not window_ids:
            return []
        return [self._dpy.create_resource_object("window", wid) for wid in window_ids]

    def get_window_desktop(self, window: Window) -> int | None:
        """Returns the desktop index for a given window, or None if unset."""
        desktop = get_property_value(window, self._atom.wm_desktop)
        return desktop[0] if desktop else None

    # --- Frame extents ---

    def get_gtk_frame_extents(self, window: Window) -> FrameExtents | None:
        """Returns _GTK_FRAME_EXTENTS (CSD shadow) if present."""
        extents = get_property_value(window, self._atom.gtk_frame_extents)
        if extents and len(extents) >= 4:
            return FrameExtents(extents[0], extents[1], extents[2], extents[3])
        return None

    def get_net_frame_extents(self, window: Window) -> FrameExtents | None:
        """Returns _NET_FRAME_EXTENTS (titlebar + borders) if present."""
        extents = get_property_value(window, self._atom.frame_extents)
        if extents and len(extents) >= 4:
            return FrameExtents(extents[0], extents[1], extents[2], extents[3])
        return None

    # --- State predicates ---

    def _is_state_set(self, window: Window, flag: int) -> bool:
        state = get_property_value(window, self._atom.wm_state)
        return bool(state and flag in state)

    def is_window_maximized_vertically(self, window: Window) -> bool:
        return self._is_state_set(window, self._atom.v_max)

    def is_window_maximized_horizontally(self, window: Window) -> bool:
        return self._is_state_set(window, self._atom.h_max)

    def is_window_fullscreen(self, window: Window) -> bool:
        return self._is_state_set(window, self._atom.fullscreen)

    # --- State writes ---

    def set_max_flags(self, window: Window, v: int = 1, h: int = 1) -> None:
        """Set or clear _NET_WM_STATE_MAXIMIZED_VERT / _MAXIMIZED_HORZ."""
        self._send_wm_state(window, v, self._atom.v_max)
        self._send_wm_state(window, h, self._atom.h_max)

    def set_fullscreen_flag(self, window: Window, on: bool) -> None:
        """Set or clear _NET_WM_STATE_FULLSCREEN."""
        self._send_wm_state(window, 1 if on else 0, self._atom.fullscreen)

    def _send_wm_state(self, window: Window, action: int, atom: int) -> None:
        """Send a _NET_WM_STATE client message to the root window.

        Action: 0=remove, 1=add, 2=toggle per EWMH §_NET_WM_STATE.
        """
        event = protocol.event.ClientMessage(
            window=window,
            client_type=self._atom.wm_state,
            data=(32, [action, atom, 0, 0, 0]),
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        self._root.send_event(event, event_mask=mask)
