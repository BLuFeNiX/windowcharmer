import logging
from collections.abc import Sequence
from typing import cast

from Xlib import X
from Xlib.display import Display
from Xlib.error import BadDrawable, BadWindow
from Xlib.xobject.drawable import Window

logger = logging.getLogger(__name__)


class AtomCache:
    """Lazily interns X11 atoms by name and exposes them as typed properties."""

    def __init__(self, dpy: Display) -> None:
        self._d = dpy
        self._cache: dict[str, int] = {}

    def _get(self, name: str) -> int:
        if name not in self._cache:
            self._cache[name] = self._d.intern_atom(name)
        return self._cache[name]

    @property
    def wm_state(self) -> int:
        return self._get("_NET_WM_STATE")

    @property
    def v_max(self) -> int:
        return self._get("_NET_WM_STATE_MAXIMIZED_VERT")

    @property
    def h_max(self) -> int:
        return self._get("_NET_WM_STATE_MAXIMIZED_HORZ")

    @property
    def fullscreen(self) -> int:
        return self._get("_NET_WM_STATE_FULLSCREEN")

    @property
    def current_desktop(self) -> int:
        return self._get("_NET_CURRENT_DESKTOP")

    @property
    def wm_desktop(self) -> int:
        return self._get("_NET_WM_DESKTOP")

    @property
    def workarea(self) -> int:
        return self._get("_NET_WORKAREA")

    @property
    def active_window(self) -> int:
        return self._get("_NET_ACTIVE_WINDOW")

    @property
    def frame_extents(self) -> int:
        return self._get("_NET_FRAME_EXTENTS")

    @property
    def gtk_frame_extents(self) -> int:
        return self._get("_GTK_FRAME_EXTENTS")

    @property
    def client_list(self) -> int:
        return self._get("_NET_CLIENT_LIST")

    @property
    def client_list_stacking(self) -> int:
        return self._get("_NET_CLIENT_LIST_STACKING")


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
