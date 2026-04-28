import logging
from collections.abc import Sequence
from typing import cast

from Xlib import X
from Xlib.display import Display
from Xlib.error import BadDrawable, BadWindow
from Xlib.xobject.drawable import Window

logger = logging.getLogger(__name__)


class Atoms:
    """Interned X11 atoms. Read once at startup; constants thereafter."""

    def __init__(self, dpy: Display) -> None:
        intern = dpy.intern_atom
        self.wm_state             = intern("_NET_WM_STATE")
        self.v_max                = intern("_NET_WM_STATE_MAXIMIZED_VERT")
        self.h_max                = intern("_NET_WM_STATE_MAXIMIZED_HORZ")
        self.fullscreen           = intern("_NET_WM_STATE_FULLSCREEN")
        self.current_desktop      = intern("_NET_CURRENT_DESKTOP")
        self.wm_desktop           = intern("_NET_WM_DESKTOP")
        self.workarea             = intern("_NET_WORKAREA")
        self.active_window        = intern("_NET_ACTIVE_WINDOW")
        self.frame_extents        = intern("_NET_FRAME_EXTENTS")
        self.gtk_frame_extents    = intern("_GTK_FRAME_EXTENTS")
        self.client_list          = intern("_NET_CLIENT_LIST")
        self.client_list_stacking = intern("_NET_CLIENT_LIST_STACKING")


def get_property_value(window: Window, atom: int, property_type: int = X.AnyPropertyType) -> Sequence[int] | None:
    """Fetch a window property value, returning None on failure or absence.

    BadWindow/BadDrawable (window destroyed mid-call) are swallowed; connection
    errors and other XErrors propagate so callers can react to a dead display.
    """
    try:
        prop = window.get_full_property(atom, property_type)
        if prop and prop.value is not None:
            return cast(Sequence[int], prop.value)
    except (BadWindow, BadDrawable) as e:
        logger.debug("get_property_value failed: %s", e)
    return None
