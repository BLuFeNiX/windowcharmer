import threading
import sys
import argparse
import logging
from Xlib import X

# Components
from .window_manager import WindowManager
from .input_handler import KeyGrabber
from .sleep_detector import WakeFromSleepDetector
from .key_monitor import KeyMonitor
from .keyboard_mapper import KeyboardMapper
import pyudev
import traceback

logger = logging.getLogger(__name__)

class WindowCharmerApp:
    def __init__(self, debug=False):
        self.debug = debug
        self.wm = WindowManager()
        self.mapper = KeyboardMapper()
        
        # State
        self.super_pressed = False
        self.key_pressed_while_super_down = False
        
        # Timers
        self.debounce_timer = None
        self.timer_lock = threading.Lock()

        # Connect display for grabbing hotkeys (needs dedicated connection)
        from Xlib import display
        self.grab_dpy = display.Display()

    def do_action(self, action):
        """Execute a window manager action (tile, center, etc.)"""
        self.wm.execute_action(action)

    def _setup_key_bindings(self):
        """Define the hotkey -> action mapping."""
        return {
            'Up':           lambda: self.do_action("max"),
            'Down':         lambda: self.do_action("center"),
            'Left':         lambda: self.do_action("left"),
            'Right':        lambda: self.do_action("right"),
            'space':        lambda: self.do_action("restore"),

            'KP_Home':      lambda: self.do_action("top-left"),
            'KP_Up':        lambda: self.do_action("top-center"),
            'KP_Page_Up':   lambda: self.do_action("top-right"),
            'KP_Left':      lambda: self.do_action("left"),
            'KP_Begin':     lambda: self.do_action("center"),
            'KP_Right':     lambda: self.do_action("right"),
            'KP_End':       lambda: self.do_action("bottom-left"),
            'KP_Down':      lambda: self.do_action("bottom-center"),
            'KP_Page_Down': lambda: self.do_action("bottom-right"),
            'KP_Insert':    lambda: self.do_action("restore"),

            'KP_Prior':     lambda: self.do_action("top-right"),
            'KP_Next':      lambda: self.do_action("bottom-right"),

            'KP_Add':       lambda: self.do_action("bigger"),
            'KP_Subtract':  lambda: self.do_action("smaller"),

            'BackSpace':    lambda: sys.exit(),
        }

    def _handle_rebind_request(self, event=None):
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

        # Delegate the actual logic to the mapper
        self.mapper.apply_super_hyper_swap()

    def _monitor_callback(self, dpy, event):
        """
        Callback for the low-level KeyMonitor (XRecord).
        Handles updating keycode cache and detecting Super key passthrough.
        """
        # 1. Handle Mapping Changes
        if event.type == X.MappingNotify:
            self.mapper.refresh_keycodes()
            return

        # 2. Handle Key Press/Release for Super/Hyper Passthrough Logic
        if event.type == X.KeyPress or event.type == X.KeyRelease:
            # Check against current keycodes from mapper
            if event.detail == self.mapper.super_l_keycode:
                if event.type == X.KeyPress:
                    self.super_pressed = True
                    logger.debug("Super_L key pressed")
                elif event.type == X.KeyRelease:
                    self.super_pressed = False
                    logger.debug("Super_L key released")
                    if not self.key_pressed_while_super_down:
                        logger.debug("Forwarding super press")
                        self.mapper.simulate_hyper_press()
                    self.key_pressed_while_super_down = False
            elif self.super_pressed and event.type == X.KeyPress:
                self.key_pressed_while_super_down = True

    def _schedule_rebind(self):
        """Debounce the rebind call for udev events."""
        with self.timer_lock:
            if self.debounce_timer:
                self.debounce_timer.cancel()
            self.debounce_timer = threading.Timer(0.25, self._handle_rebind_request)
            self.debounce_timer.start()

    def _monitor_input_events(self):
        """Monitor udev for keyboard plug/unplug events."""
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='input')
        for device in iter(monitor.poll, None):
            if device.action == 'add' and device.properties.get('DEVNAME', '').startswith('/dev/input/event'):
                logger.info(f"Input device added, scheduling rebind...")
                self._schedule_rebind()

    def run_daemon(self):
        logger.info("Starting WindowCharmer Daemon...")

        # 1. Initial Key Swap
        self.mapper.apply_super_hyper_swap()

        # 2. Start Key Monitor (Passthrough Logic)
        from Xlib import display
        monitor_dpy = display.Display()
        monitor = KeyMonitor(monitor_dpy, self._monitor_callback)
        t_mon = threading.Thread(target=monitor.start)
        t_mon.daemon = True
        t_mon.start()

        # 3. Start Sleep Detector
        detector = WakeFromSleepDetector(callback=lambda: self._handle_rebind_request())
        t_sleep = threading.Thread(target=detector.start)
        t_sleep.daemon = True
        t_sleep.start()

        # 4. Start Udev Monitor
        t_udev = threading.Thread(target=self._monitor_input_events)
        t_udev.daemon = True
        t_udev.start()

        # 5. Start Main Hotkey Grabber (Blocking Loop)
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
            self.mapper.cleanup()

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("action", nargs='?', help="Action to perform", 
        choices=[
            'left', 'center', 'right', 'top-left', 'bottom-left',
            'top-right', 'bottom-right', 'top-center', 'bottom-center',
            'max', 'restore', 'cycle', 'install', 'bigger', 'smaller', 'test'
        ]
    )
    group.add_argument("-d", "--daemonize", action="store_true", help="Run as a daemon")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger('windowcharmer').setLevel(logging.DEBUG)

    app = WindowCharmerApp(debug=args.debug)

    if args.daemonize:
        app.run_daemon()
    else:
        app.do_action(args.action)

if __name__ == "__main__":
    main()
