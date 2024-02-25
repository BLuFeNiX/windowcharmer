from Xlib import X, XK, display
from Xlib.ext import xtest
from itertools import combinations

def grab_key_ignore_locks(dpy, keycode, modifier=0, grab=True):
    root = dpy.screen().root
    lock_masks = [0, X.LockMask, X.Mod2Mask]

    # Generate all combinations of the lock masks
    mask_combinations = [0]  # Start with 0 to include the case with no lock masks
    for i in range(1, len(lock_masks) + 1):
        for combo in combinations(lock_masks, i):
            mask_combinations.append(sum(combo))

    for mask in mask_combinations:
        if grab:
            root.grab_key(keycode, modifier | mask, True, X.GrabModeAsync, X.GrabModeAsync)
        else:
            root.ungrab_key(keycode, modifier | mask, root)

def simulate_key_press_release(dpy, keycode):
    xtest.fake_input(dpy, X.KeyPress, keycode)
    xtest.fake_input(dpy, X.KeyRelease, keycode)
    dpy.flush()

def get_keycode(dpy, keystring):
    return dpy.keysym_to_keycode(XK.string_to_keysym(keystring))

class KeyMonitor:
    def __init__(self, display, key_combinations):
        self.display = display
        self.key_combinations = key_combinations
        self.super_down = False
        self.key_pressed_while_super_down = False

    def start(self):
        # Grab the Super_L key, ignoring lock keys
        super_l_keycode = get_keycode(self.display, 'Super_L')
        grab_key_ignore_locks(self.display, super_l_keycode, grab=True)

        print("Listening for keys...")

        while True:
            event = self.display.next_event()

            # Check for Super_L key press and release
            if event.type == X.KeyPress and event.detail == super_l_keycode:
                print("Super_L key pressed")
                self.super_down = True
                # Grab all specified keys with Super_L as modifier
                for key in self.key_combinations:
                    keycode = get_keycode(self.display, key)
                    grab_key_ignore_locks(self.display, keycode, X.Mod4Mask, grab=True)

            elif event.type == X.KeyRelease and event.detail == super_l_keycode:
                print("Super_L key released")
                self.super_down = False
                # Ungrab all specified keys
                for key in self.key_combinations:
                    keycode = get_keycode(self.display, key)
                    grab_key_ignore_locks(self.display, keycode, X.Mod4Mask, grab=False)

                if not self.key_pressed_while_super_down:
                    # this is important for preserving the user experience of merely tapping Super
                    #  ex: to open the application menu
                    print("forwarding Super tap")
                    # temporarily ungrab Super_L key
                    grab_key_ignore_locks(self.display, super_l_keycode, grab=False)
                    # simulate key press
                    simulate_key_press_release(self.display, super_l_keycode)
                    # grab again
                    grab_key_ignore_locks(self.display, super_l_keycode, grab=True)

                self.key_pressed_while_super_down = False

            elif self.super_down and event.type == X.KeyPress:
                keycode = event.detail
                for key, action in self.key_combinations.items():
                    if keycode == get_keycode(self.display, key):
                        action()
                        self.key_pressed_while_super_down = True
                        break

def do_action(action):
    print(f"Action: {action}")

if __name__ == "__main__":
    dpy = display.Display()
    key_combinations = {
        'Up':          lambda: do_action("max"),           # Up
        'Down':        lambda: do_action("center"),        # Down
        'Left':        lambda: do_action("left"),          # Left
        'Right':       lambda: do_action("right"),         # Right
        'Space':       lambda: do_action("restore"),       # Spacebar

        'KP_Home':     lambda: do_action("top-left"),      # Numpad 7
        'KP_Up':       lambda: do_action("top-center"),    # Numpad 8
        'KP_Page_Up':  lambda: do_action("top-right"),     # Numpad 9
        'KP_Left':     lambda: do_action("left"),          # Numpad 4
        'KP_Begin':    lambda: do_action("center"),        # Numpad 5
        'KP_Right':    lambda: do_action("right"),         # Numpad 6
        'KP_End':      lambda: do_action("bottom-left"),   # Numpad 1
        'KP_Down':     lambda: do_action("bottom-center"), # Numpad 2
        'KP_Page_Down':lambda: do_action("bottom-right"),  # Numpad 3
        'KP_Insert':   lambda: do_action("restore"),       # Numpad 0

        'KP_Prior':    lambda: do_action("top-right"),     # Numpad 9 (alternate keyboard layout)
        'KP_Next':     lambda: do_action("bottom-right"),  # Numpad 3 (alternate keyboard layout)

        'KP_Add':      lambda: do_action("bigger"),        # Numpad +
        'KP_Subtract': lambda: do_action("smaller"),       # Numpad -
    }

    monitor = KeyMonitor(dpy, key_combinations)
    monitor.start()
