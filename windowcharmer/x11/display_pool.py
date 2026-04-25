import logging
import threading
from typing import ClassVar

from Xlib import display
from Xlib.display import Display

logger = logging.getLogger(__name__)


class DisplayPool:
    """Manages named Display connections, one per role (wm, mapper, monitor, grabber).

    Each name must only ever be accessed from one thread — the pool provides
    role-based, not thread-based, separation.
    """

    _displays: ClassVar[dict[str, Display]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

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
