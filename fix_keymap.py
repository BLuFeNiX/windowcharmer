from Xlib import X, XK, display

def change_keyboard_mapping(dpy, keycode, new_keysym):
    """Change the keyboard mapping for a single keycode."""
    keysyms = [(new_keysym,)]  # Tuple of keysyms for each keycode
    dpy.change_keyboard_mapping(keycode, keysyms)
    dpy.flush()

def main():
    dpy = display.Display()
    change_keyboard_mapping(dpy, 133, XK.string_to_keysym('Super_L'))
    change_keyboard_mapping(dpy, 207, XK.string_to_keysym('Hyper_L'))

if __name__ == '__main__':
    main()
