import threading
import sys
import argparse
import logging
from typing import Callable, Any
from Xlib import X

# Components
from .tiling.manager import WindowManager
from .input.key_grabber import KeyGrabber
from .x11.keyboard_mapper import KeyboardMapper
from .input.services import InputServices
from .config import KeyBindings, TileAction
from .input.super_passthrough import SuperPassthroughTracker
import traceback

logger = logging.getLogger(__name__)

class WindowCharmerApp:
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        self.wm = WindowManager()
        self.key_bindings = KeyBindings.load()
        
        self.passthrough_tracker: SuperPassthroughTracker | None = None
        
        # Timers
        self.debounce_timer: threading.Timer | None = None
        self.timer_lock = threading.Lock()

        # Deferred initialization for daemon components
        self.mapper: KeyboardMapper | None = None
        self.grab_dpy: Any | None = None
        self.input_services: InputServices | None = None

    def do_action(self, action: TileAction) -> None:
        """Execute a window manager action (tile, center, etc.)"""
        if action == TileAction.EXIT:
            sys.exit()
        self.wm.execute_action(action)

    def _setup_key_bindings(self) -> dict[str, Callable[[], None]]:
        """Define the hotkey -> action mapping."""
        # Map TileAction enum values to callables
        bindings: dict[str, Callable[[], None]] = {}
        for key, action in self.key_bindings.items():
            # We use a default argument (a=action) to capture the value in the closure
            def make_handler(a: TileAction = action) -> Callable[[], None]:
                return lambda: self.do_action(a)
            bindings[key] = make_handler()
        return bindings

    def _handle_rebind_request(self, event: Any | None = None) -> None:
        """
        Callback for when a rebind is requested (Sleep, Udev, X11 MappingNotify).
        Handles debouncing and filtering.
        """
        if event:
            if event.request == X.MappingKeyboard:
                logger.debug("MappingNotify is for Keyboard, proceeding with check...")
            else:
                logger.debug(f"MappingNotify is for {event.request}, ignoring.")
                return
        
        # If called without an event (e.g. from udev or sleep), debounce it
        if event is None:
            self._schedule_rebind()
        else:
            # If from X11 event (MappingNotify), run immediately as we are already in an event loop context
            if self.mapper:
                self.mapper.apply_super_hyper_swap()

    def _monitor_callback(self, event: Any) -> None:
        """
        Callback for the low-level KeyMonitor (XRecord).
        Handles updating keycode cache and detecting Super key passthrough.
        """
        if not self.mapper:
             return

        # 1. Handle Mapping Changes
        if event.type == X.MappingNotify:
            self.mapper.refresh_keycodes()
            if self.passthrough_tracker:
                 self.passthrough_tracker.update_keycode(self.mapper.super_l_keycode)
            return

        # 2. Handle Key Press/Release for Super/Hyper Passthrough Logic
        if event.type == X.KeyPress or event.type == X.KeyRelease:
            if self.passthrough_tracker:
                 self.passthrough_tracker.handle_event(event)

    def _schedule_rebind(self) -> None:
        """Debounce the rebind call for udev events."""
        with self.timer_lock:
            if self.debounce_timer:
                self.debounce_timer.cancel()
            
            if self.mapper:
                 self.debounce_timer = threading.Timer(0.25, self.mapper.apply_super_hyper_swap)
                 self.debounce_timer.start()

    def run_daemon(self) -> None:
        logger.info("Starting WindowCharmer Daemon...")

        # Initialize daemon-specific components
        from .x11.display_pool import DisplayPool
        self.grab_dpy = DisplayPool.get_display("grabber")
        self.mapper = KeyboardMapper()
        
        self.passthrough_tracker = SuperPassthroughTracker(
            self.mapper.super_l_keycode,
            lambda: self.mapper.simulate_hyper_press() if self.mapper else None
        )
        
        # Note: on_key_event_callback signature mismatch in InputServices vs callback
        # InputServices expects: Callable[[Any], None]
        # _monitor_callback signature: (event: Any) -> None
        # So we can pass it directly.
        
        self.input_services = InputServices(
            on_rebind_callback=self._handle_rebind_request,
            on_key_event_callback=self._monitor_callback
        )

        # 1. Initial Key Swap
        self.mapper.apply_super_hyper_swap()

        # 2. Start Background Services (Key Monitor, Sleep Monitor, Udev Monitor)
        self.input_services.start_all()

        # 3. Start Main Hotkey Grabber (Blocking Loop)
        grabber = KeyGrabber(
            self.grab_dpy, 
            self._setup_key_bindings(), 
            modifier=X.Mod4Mask,
            on_mapping_notify=self._handle_rebind_request
        )
        
        try:
            logger.info("Daemon started. Press Ctrl+C to exit.")
            grabber.start()
        except (KeyboardInterrupt, SystemExit):
            pass
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            logger.debug(traceback.format_exc())
        finally:
            if self.input_services:
                self.input_services.stop_all()
            if self.mapper:
                self.mapper.cleanup()

def main() -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    parser = argparse.ArgumentParser(description="WindowCharmer Daemon")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--fix-keymap", action="store_true", help="Fix the keyboard mapping for Super_L and Hyper_L and exit")
    
    args = parser.parse_args()

    if args.fix_keymap:
        from Xlib import XK, display
        dpy = display.Display()
        def change_keyboard_mapping(d: display.Display, keycode: int, new_keysym: int) -> None:
            keysyms = [(new_keysym,)]
            d.change_keyboard_mapping(keycode, keysyms)
            d.flush()
        
        change_keyboard_mapping(dpy, 133, XK.string_to_keysym('Super_L'))
        change_keyboard_mapping(dpy, 207, XK.string_to_keysym('Hyper_L'))
        print("Keyboard mapping fixed.")
        sys.exit(0)

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger('windowcharmer').setLevel(logging.DEBUG)

    app = WindowCharmerApp(debug=args.debug)
    app.run_daemon()

if __name__ == "__main__":
    main()
