import logging
import threading
from typing import ClassVar

from Xlib import display
from Xlib.display import Display

logger = logging.getLogger(__name__)


class DisplayPool:
    """Manages named Display connections, one per role (wm, mapper, monitor, grabber).

    The pool provides role-based, not thread-based, separation. Most roles are
    used from a single thread (wm: main, monitor: XRecord, grabber: main), but
    a connection may be shared across threads if every caller serializes access
    behind its own lock — KeyboardMapper does this for the mapper connection,
    which is touched from the main thread (apply_swap, cleanup), the debounce
    timer thread (apply_swap), and the monitor thread (refresh_keycodes,
    simulate_hyper_press). python-xlib is not internally thread-safe, so the
    lock is mandatory; this docstring is the only contract.
    """

    _displays: ClassVar[dict[str, Display]] = {}
    _lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def get_display(cls, name: str) -> Display:
        """Retrieves a display connection by name, creating it if necessary.
        Common names: 'wm', 'mapper', 'monitor', 'grabber'
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
                    logger.warning("Error closing display %r: %s", name, e)
            cls._displays.clear()
