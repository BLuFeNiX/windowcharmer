import argparse
import functools
import logging
import signal
import sys
import threading
from collections.abc import Callable

from Xlib import X
from Xlib.display import Display
from Xlib.protocol import rq

from . import __version__
from .config.actions import TileAction
from .config.keybindings import load_keybindings
from .input.key_grabber import KeyGrabber, KeyGrabberError
from .input.services import InputServices
from .input.super_passthrough import SuperPassthroughTracker
from .tiling.manager import WindowManager
from .x11.display_pool import DisplayPool
from .x11.keyboard_mapper import KeyboardMapper

logger = logging.getLogger(__name__)

_REBIND_DEBOUNCE_SECONDS = 0.25


class WindowCharmerApp:
    def __init__(self, no_animate: bool = False) -> None:
        self.wm = WindowManager(no_animate=no_animate)
        self.key_bindings = load_keybindings()
        self.timer_lock = threading.Lock()
        self.debounce_timer: threading.Timer | None = None

        self.mapper = KeyboardMapper()
        self.grab_dpy: Display = DisplayPool.get_display("grabber")

        self.passthrough_tracker = SuperPassthroughTracker(
            self.mapper.super_l_keycode,
            self.mapper.simulate_hyper_press,
        )

        self.input_services = InputServices(
            on_rebind_callback=self._schedule_rebind,
            on_key_event_callback=self._monitor_callback,
        )

        self.grabber = KeyGrabber(
            self.grab_dpy,
            self._setup_key_bindings(),
            modifier=X.Mod4Mask,
            on_mapping_notify=self._on_mapping_notify,
        )

    def stop(self) -> None:
        """Request a clean shutdown. Safe to call from a signal handler
        (KeyGrabber.stop() sets a flag and writes a single non-blocking byte
        to its wakeup pipe).
        """
        self.grabber.stop()

    def do_action(self, action: TileAction) -> None:
        """Execute a window manager action (tile, center, etc.)"""
        if action == TileAction.EXIT:
            self.stop()
            return
        self.wm.execute_action(action)

    def _setup_key_bindings(self) -> dict[str, Callable[[], None]]:
        return {key: functools.partial(self.do_action, action) for key, action in self.key_bindings.items()}

    def _schedule_rebind(self) -> None:
        """Debounce a keymap rebind — used by udev and sleep monitors."""
        with self.timer_lock:
            if self.debounce_timer:
                self.debounce_timer.cancel()
            self.debounce_timer = threading.Timer(_REBIND_DEBOUNCE_SECONDS, self.mapper.apply_super_hyper_swap)
            self.debounce_timer.start()

    def _on_mapping_notify(self, event: rq.Event) -> None:
        """Handle a MappingNotify event from the KeyGrabber."""
        if event.request != X.MappingKeyboard:
            logger.debug("MappingNotify for %s, ignoring.", event.request)
            return
        logger.debug("MappingNotify for Keyboard, applying swap...")
        self.mapper.apply_super_hyper_swap()

    def _monitor_callback(self, event: rq.Event) -> None:
        """Handle low-level XRecord events for keycode cache and Super passthrough."""
        if event.type == X.MappingNotify:
            super_kc = self.mapper.refresh_keycodes()
            self.passthrough_tracker.update_keycode(super_kc)
            return

        if event.type in (X.KeyPress, X.KeyRelease):
            self.passthrough_tracker.handle_event(event)

    def run_daemon(self) -> None:
        logger.info("Starting WindowCharmer Daemon...")
        self.mapper.apply_super_hyper_swap()
        self.input_services.start_all()

        try:
            logger.info("Daemon started. Press Ctrl+C to exit.")
            self.grabber.start()
        except KeyboardInterrupt:
            pass
        finally:
            self.grabber.ungrab_keys()
            # Stop input monitors first so no new debounce timers can be scheduled,
            # then cancel + join the in-flight one. Joining guarantees the rebind
            # has completed before mapper.cleanup() restores the canonical mapping
            # — otherwise a late-firing rebind would re-swap after cleanup.
            self.input_services.stop_all()
            with self.timer_lock:
                timer = self.debounce_timer
                self.debounce_timer = None
            if timer:
                timer.cancel()
                timer.join()
                logger.debug("Joined debounce timer at shutdown")
            self.mapper.cleanup()
            DisplayPool.close_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="WindowCharmer Daemon")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--no-animate", action="store_true", help="Disable Cinnamon compositor animations")
    parser.add_argument("--fix-keymap", action="store_true", help="Restore canonical Super_L/Hyper_L mapping and exit")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    if args.fix_keymap:
        try:
            mapper = KeyboardMapper()
            if not mapper.force_canonical():
                print("Super_L or Hyper_L not found in keyboard mapping.", file=sys.stderr)
                sys.exit(1)
            print("Keyboard mapping fixed.")
        finally:
            DisplayPool.close_all()
        sys.exit(0)

    app = WindowCharmerApp(no_animate=args.no_animate)
    # Install AFTER construction so the handler can reference app. stop() is
    # signal-safe (sets a flag and writes one byte non-blocking), which wakes
    # the loop immediately rather than waiting for the next X event.
    # SIGHUP is handled too so a session-end or `kill -HUP` runs cleanup —
    # otherwise the keymap stays inverted until next X login.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: app.stop())
    try:
        app.run_daemon()
    except KeyGrabberError as e:
        logger.error("Daemon stopped: %s", e)
        logger.debug("", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
