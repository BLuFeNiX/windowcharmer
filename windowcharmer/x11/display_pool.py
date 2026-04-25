import threading
import logging
from Xlib import display
from Xlib.display import Display

logger = logging.getLogger(__name__)

class DisplayPool:
    """
    Manages named Display connections. Each name must only ever be accessed from one
    thread; the pool provides role-based, not thread-based, separation.
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
                except Exception as e:
                    logger.warning(f"Error closing display {name!r}: {e}")
            cls._displays.clear()
