from Xlib import X

class AtomCache:
    def __init__(self, dpy):
        self.d = dpy
        self._cache = {}
        self._atom_names = {
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

    def __getattr__(self, name):
        if name in self._atom_names:
            atom_name = self._atom_names[name]
            if atom_name not in self._cache:
                self._cache[atom_name] = self.d.intern_atom(atom_name)
            return self._cache[atom_name]
        else:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

def get_property_value(window, atom, property_type=X.AnyPropertyType):
    """Helper to get a window property value cleanly."""
    try:
        prop = window.get_full_property(atom, property_type)
        if prop and prop.value is not None:
             # Handle list-like values (most properties) by returning the raw value
             # which is usually a tuple or array.
             # For single values, caller often does val[0].
             return prop.value
    except Exception:
        pass
    return None
