import argparse
import sys
import threading
import time
import pyudev
import traceback
from Xlib import X, XK, display
from Xlib.ext import xtest

from .window_manager import WindowManager
from .input_handler import KeyGrabber
from .sleep_detector import WakeFromSleepDetector
from .key_monitor import KeyMonitor

# Global manager instance
wm = WindowManager()

def do_action(action):
    wm.execute_action(action)

def change_keyboard_mapping(dpy, keycode, new_keysym):
    keysyms = [(new_keysym,)]
    dpy.change_keyboard_mapping(keycode, keysyms)
    dpy.flush()

def simulate_key_press_release(dpy, keycode):
    xtest.fake_input(dpy, X.KeyPress, keycode)
    xtest.fake_input(dpy, X.KeyRelease, keycode)
    dpy.flush()

def daemonize():
    # Key bindings
    key_combinations = {
        'Up':           lambda: do_action("max"),
        'Down':         lambda: do_action("center"),
        'Left':         lambda: do_action("left"),
        'Right':        lambda: do_action("right"),
        'space':        lambda: do_action("restore"),

        'KP_Home':      lambda: do_action("top-left"),
        'KP_Up':        lambda: do_action("top-center"),
        'KP_Page_Up':   lambda: do_action("top-right"),
        'KP_Left':      lambda: do_action("left"),
        'KP_Begin':     lambda: do_action("center"),
        'KP_Right':     lambda: do_action("right"),
        'KP_End':       lambda: do_action("bottom-left"),
        'KP_Down':      lambda: do_action("bottom-center"),
        'KP_Page_Down': lambda: do_action("bottom-right"),
        'KP_Insert':    lambda: do_action("restore"),

        'KP_Prior':     lambda: do_action("top-right"),
        'KP_Next':      lambda: do_action("bottom-right"),

        'KP_Add':       lambda: do_action("bigger"),
        'KP_Subtract':  lambda: do_action("smaller"),

        'BackSpace':    lambda: sys.exit(),
    }

    # Shared display for daemon operations (mapping, simulation)
    # Protected by a lock to allow safe usage from multiple threads (udev, sleep, monitor)
    daemon_dpy = display.Display()
    daemon_lock = threading.Lock()
    
    # Calculate keycodes/keysyms once
    super_l_keycode = daemon_dpy.keysym_to_keycode(XK.string_to_keysym('Super_L'))
    hyper_l_keycode = daemon_dpy.keysym_to_keycode(XK.string_to_keysym('Hyper_L'))
    super_l_keysym = XK.string_to_keysym('Super_L')
    hyper_l_keysym = XK.string_to_keysym('Hyper_L')

    # Backup original mappings
    super_l_orig = daemon_dpy.get_keyboard_mapping(super_l_keycode, 1)
    try:
        hyper_l_orig = daemon_dpy.get_keyboard_mapping(hyper_l_keycode, 1)
    except Exception:
        print("Error: No mapping for Hyper_L. Exiting.")
        sys.exit(1)

    def rebind_super():
        print("Swapping Super_L and Hyper_L...")
        with daemon_lock:
            try:
                change_keyboard_mapping(daemon_dpy, super_l_keycode, hyper_l_keysym)
                change_keyboard_mapping(daemon_dpy, hyper_l_keycode, super_l_keysym)
                daemon_dpy.sync()
            except Exception as e:
                print(f"Error rebinding keys: {e}")
                traceback.print_exc()

    # Initial rebind
    rebind_super()

    # State for Super key passthrough
    super_pressed = False
    key_pressed_while_super_down = False
    
    # We define the callback inside daemonize to access local vars
    def monitor_callback(dpy, event):
        nonlocal super_pressed, key_pressed_while_super_down
        
        # NOTE: 'dpy' here is the connection from KeyMonitor, do not use it for simulation
        
        if event.type == X.KeyPress or event.type == X.KeyRelease:
            if event.detail == super_l_keycode:
                if event.type == X.KeyPress:
                    super_pressed = True
                    # print("Super_L key pressed")
                elif event.type == X.KeyRelease:
                    super_pressed = False
                    # print("Super_L key released")
                    if not key_pressed_while_super_down:
                        # print("Forwarding super press")
                        with daemon_lock:
                            try:
                                # We simulate Hyper_L keycode, which we mapped to Super_L keysym
                                simulate_key_press_release(daemon_dpy, hyper_l_keycode)
                            except Exception as e:
                                print(f"Error simulating key: {e}")
                    key_pressed_while_super_down = False
            elif super_pressed and event.type == X.KeyPress:
                key_pressed_while_super_down = True

    # Start low-level key monitor (RECORD extension)
    # This MUST use its own display connection (legacy note about CPU bug)
    monitor_dpy = display.Display()
    monitor = KeyMonitor(monitor_dpy, monitor_callback)
    t_mon = threading.Thread(target=monitor.start)
    t_mon.daemon = True
    t_mon.start()

    # Sleep / Wake detection
    detector = WakeFromSleepDetector(callback=rebind_super)
    t_sleep = threading.Thread(target=detector.start)
    t_sleep.daemon = True
    t_sleep.start()

    # Udev monitoring for keyboard plug-in
    debounce_timer = None
    timer_lock = threading.Lock()

    def schedule_rebind():
        nonlocal debounce_timer
        with timer_lock:
            if debounce_timer:
                debounce_timer.cancel()
            debounce_timer = threading.Timer(0.25, rebind_super)
            debounce_timer.start()

    def monitor_input_events():
        context = pyudev.Context()
        monitor = pyudev.Monitor.from_netlink(context)
        monitor.filter_by(subsystem='input')
        for device in iter(monitor.poll, None):
            if device.action == 'add' and device.properties.get('DEVNAME', '').startswith('/dev/input/event'):
                print(f"Input device added, scheduling rebind...")
                schedule_rebind()

    t_udev = threading.Thread(target=monitor_input_events)
    t_udev.daemon = True
    t_udev.start()

    # Start main key grabber loop
    # Needs its own display connection for grabs because it blocks on next_event
    grab_dpy = display.Display()
    grabber = KeyGrabber(grab_dpy, key_combinations, modifier=X.Mod4Mask)
    
    try:
        print("Daemon started. Press Ctrl+C to exit.")
        grabber.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        print(f"Error in main loop: {e}")
        traceback.print_exc()
    finally:
        print("Restoring keyboard mapping...")
        # Use a fresh connection for cleanup to ensure it works even if daemon_dpy is borked
        try:
            d = display.Display()
            d.change_keyboard_mapping(super_l_keycode, super_l_orig)
            d.change_keyboard_mapping(hyper_l_keycode, hyper_l_orig)
            d.sync()
            d.close()
        except:
            pass

def main():
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
    
    args = parser.parse_args()

    if args.daemonize:
        print("Starting WindowCharmer Daemon...")
        daemonize()
    else:
        do_action(args.action)

if __name__ == "__main__":
    main()
