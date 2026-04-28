import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)


class RebindScheduler:
    """Debounces calls into a single callback fired after a quiet period.

    Two callers schedule rebinds: the sleep monitor (background thread) and
    the InputManager's HierarchyChanged handler (main thread). Both want the
    same effect — re-apply the keymap swap once the burst of triggers settles
    — so this class collapses them behind a single timer with a lock.

    `shutdown()` cancels and joins any in-flight timer. Joining is what makes
    cleanup correct: otherwise a late-firing rebind would re-apply the swap
    after the daemon's restoration ran, leaving the keymap inverted at exit.
    """

    def __init__(self, callback: Callable[[], None], delay_seconds: float) -> None:
        self._callback = callback
        self._delay = delay_seconds
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def schedule(self) -> None:
        """Schedule the callback to fire after the debounce delay.

        If a timer is already pending, it's cancelled and replaced.
        """
        with self._lock:
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self._delay, self._callback)
            self._timer.start()

    def shutdown(self) -> None:
        """Cancel any pending timer and wait for an in-flight fire to finish.

        After this returns, no further callback invocation is possible from a
        previously scheduled timer (new schedule() calls would still arm one,
        so callers must stop scheduling before shutting down).
        """
        with self._lock:
            timer = self._timer
            self._timer = None
        if timer:
            timer.cancel()
            timer.join()
            logger.debug("Joined debounce timer at shutdown")
