from Xlib import X, XK, display
from itertools import combinations
import sys
import traceback

def grab_key_ignore_locks(dpy, keycode, modifier=0, grab=True):
    root = dpy.screen().root
    lock_masks = [X.LockMask, X.Mod2Mask]

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

def get_keycode(dpy, keystring):
    return dpy.keysym_to_keycode(XK.string_to_keysym(keystring))

class KeyGrabber:
    def __init__(self, dpy, key_combinations, modifier=0):
        self.dpy = dpy
        # we accept nicely named keys like "Left", so convert them to keycode integers
        self.keycode_action_map = {get_keycode(dpy, key): value for key, value in key_combinations.items()}
        if 0 in self.keycode_action_map:
            raise ValueError("refusing to bind to keycode 0 (all keys)!")
        self.modifier = modifier

    def start(self):
        try:
            for keycode in self.keycode_action_map:
                grab_key_ignore_locks(self.dpy, keycode, modifier=self.modifier, grab=True)
            while True:
                event = self.dpy.next_event()
                if event.type == X.KeyPress:
                    action_func = self.keycode_action_map.get(event.detail)
                    if action_func:
                        action_func()
        except:
            print("Exiting KeyGrab event loop!")
            raise
        finally:
            for keycode in self.keycode_action_map:
                grab_key_ignore_locks(self.dpy, keycode, modifier=self.modifier, grab=False)


if __name__ == "__main__":

    def do_action(action):
        print(f"Action: {action}")

    key_combinations = {
        'Up':           lambda: do_action("max"),           # Up
        'Down':         lambda: do_action("center"),        # Down
        'Left':         lambda: do_action("left"),          # Left
        'Right':        lambda: do_action("right"),         # Right
        'space':        lambda: do_action("restore"),       # spacebar

        'KP_Home':      lambda: do_action("top-left"),      # Numpad 7
        'KP_Up':        lambda: do_action("top-center"),    # Numpad 8
        'KP_Page_Up':   lambda: do_action("top-right"),     # Numpad 9
        'KP_Left':      lambda: do_action("left"),          # Numpad 4
        'KP_Begin':     lambda: do_action("center"),        # Numpad 5
        'KP_Right':     lambda: do_action("right"),         # Numpad 6
        'KP_End':       lambda: do_action("bottom-left"),   # Numpad 1
        'KP_Down':      lambda: do_action("bottom-center"), # Numpad 2
        'KP_Page_Down': lambda: do_action("bottom-right"),  # Numpad 3
        'KP_Insert':    lambda: do_action("restore"),       # Numpad 0

        'KP_Prior':     lambda: do_action("top-right"),     # Numpad 9 (alternate keyboard layout)
        'KP_Next':      lambda: do_action("bottom-right"),  # Numpad 3 (alternate keyboard layout)

        'KP_Add':       lambda: do_action("bigger"),        # Numpad +
        'KP_Subtract':  lambda: do_action("smaller"),       # Numpad -

        'BackSpace':    lambda: sys.exit(),                 # backspace
    }

    dpy = display.Display()
    try:
        grabber = KeyGrabber(dpy, key_combinations, modifier=X.Mod4Mask)
        grabber.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    except:
        print("Unexpected error:", sys.exc_info()[0])
        traceback.print_exc()
    finally:
        dpy.flush()
