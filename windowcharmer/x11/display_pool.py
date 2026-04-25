import threading
from Xlib import display
from Xlib.display import Display

class DisplayPool:
    """
    Manages centralized Display connections.
    Xlib requires a separate Display connection per thread for safe concurrent access.
    """
    _displays: dict[str, Display] = {}
    _lock = threading.Lock()

    @classmethod
    def get_display(cls, name: str) -> Display:
        """
        Retrieves a display connection by name, creating it if necessary.
        Common names: 'main', 'wm', 'mapper', 'monitor'
        """
        with cls._lock:
            if name not in cls._displays:
                cls._displays[name] = display.Display()
            return cls._displays[name]

    @classmethod
    def close_all(cls) -> None:
        """Closes all open display connections."""
        with cls._lock:
            for name, dpy in cls._displays.items():
                try:
                    dpy.close()
                except Exception:
                    pass
            cls._displays.clear()
