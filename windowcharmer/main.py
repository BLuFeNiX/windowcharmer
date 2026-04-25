import argparse
import functools
import logging
import signal
import sys
import threading
from collections.abc import Callable
from typing import Any

from Xlib import X
from Xlib.display import Display

from .config import TileAction, load_keybindings
from .input.key_grabber import KeyGrabber
from .input.services import InputServices
from .input.super_passthrough import SuperPassthroughTracker
from .tiling.manager import WindowManager
from .x11.keyboard_mapper import KeyboardMapper

logger = logging.getLogger(__name__)

_REBIND_DEBOUNCE_SECONDS = 0.25


class WindowCharmerApp:
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        self.wm = WindowManager()
        self.key_bindings = load_keybindings()

        self.passthrough_tracker: SuperPassthroughTracker | None = None

        self.debounce_timer: threading.Timer | None = None
        self.timer_lock = threading.Lock()

        self.mapper: KeyboardMapper | None = None
        self.grab_dpy: Display | None = None
        self.input_services: InputServices | None = None
        self.grabber: KeyGrabber | None = None

    def do_action(self, action: TileAction) -> None:
        """Execute a window manager action (tile, center, etc.)"""
        if action == TileAction.EXIT:
            sys.exit()
        self.wm.execute_action(action)

    def _setup_key_bindings(self) -> dict[str, Callable[[], None]]:
        return {key: functools.partial(self.do_action, action) for key, action in self.key_bindings.items()}

    def _schedule_rebind(self) -> None:
        """Debounce a keymap rebind — used by udev and sleep monitors."""
        with self.timer_lock:
            if self.debounce_timer:
                self.debounce_timer.cancel()
            if self.mapper:
                self.debounce_timer = threading.Timer(_REBIND_DEBOUNCE_SECONDS, self.mapper.apply_super_hyper_swap)
                self.debounce_timer.start()

    def _on_mapping_notify(self, event: Any) -> None:
        """Handle a MappingNotify event from the KeyGrabber."""
        if event.request != X.MappingKeyboard:
            logger.debug(f"MappingNotify for {event.request}, ignoring.")
            return
        logger.debug("MappingNotify for Keyboard, applying swap...")
        if self.mapper:
            self.mapper.apply_super_hyper_swap()

    def _monitor_callback(self, event: Any) -> None:
        """Handle low-level XRecord events for keycode cache and Super passthrough."""
        if not self.mapper:
            return

        if event.type == X.MappingNotify:
            self.mapper.refresh_keycodes()
            if self.passthrough_tracker:
                self.passthrough_tracker.update_keycode(self.mapper.super_l_keycode)
            return

        if event.type in (X.KeyPress, X.KeyRelease) and self.passthrough_tracker:
            self.passthrough_tracker.handle_event(event)

    def run_daemon(self) -> None:
        logger.info("Starting WindowCharmer Daemon...")

        from .x11.display_pool import DisplayPool
        self.grab_dpy = DisplayPool.get_display("grabber")
        mapper = self.mapper = KeyboardMapper()

        self.passthrough_tracker = SuperPassthroughTracker(
            mapper.super_l_keycode,
            mapper.simulate_hyper_press,
        )

        self.input_services = InputServices(
            on_rebind_callback=self._schedule_rebind,
            on_key_event_callback=self._monitor_callback,
        )

        self.mapper.apply_super_hyper_swap()
        self.input_services.start_all()

        self.grabber = KeyGrabber(
            self.grab_dpy,
            self._setup_key_bindings(),
            modifier=X.Mod4Mask,
            on_mapping_notify=self._on_mapping_notify,
        )

        try:
            logger.info("Daemon started. Press Ctrl+C to exit.")
            self.grabber.start()
        except (KeyboardInterrupt, SystemExit):
            pass
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            logger.debug("", exc_info=True)
        finally:
            with self.timer_lock:
                if self.debounce_timer:
                    self.debounce_timer.cancel()
            if self.grabber:
                self.grabber.ungrab_keys()
            if self.input_services:
                self.input_services.stop_all()
            if self.mapper:
                self.mapper.cleanup()
            DisplayPool.close_all()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    parser = argparse.ArgumentParser(description="WindowCharmer Daemon")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--fix-keymap", action="store_true", help="Restore canonical Super_L/Hyper_L mapping and exit")

    args = parser.parse_args()

    if args.fix_keymap:
        from .x11.display_pool import DisplayPool
        try:
            mapper = KeyboardMapper()
            if not mapper.force_canonical():
                print("Super_L or Hyper_L not found in keyboard mapping.", file=sys.stderr)
                sys.exit(1)
            print("Keyboard mapping fixed.")
        finally:
            DisplayPool.close_all()
        sys.exit(0)

    if args.debug:
        logging.getLogger('windowcharmer').setLevel(logging.DEBUG)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    app = WindowCharmerApp(debug=args.debug)
    app.run_daemon()


if __name__ == "__main__":
    main()
