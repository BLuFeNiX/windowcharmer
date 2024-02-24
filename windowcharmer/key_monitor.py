from Xlib import X, XK, display
from Xlib.ext import record
from Xlib.protocol import rq

class KeyMonitor:
    def __init__(self, local_dpy, record_dpy, key_combinations):
        self.local_dpy = local_dpy
        self.record_dpy = record_dpy
        self.key_combinations = key_combinations  # Expected format: {('Control', 'c'): func_to_call}
        self.pressed_keys = set()

    def lookup_keysym(self, keysym):
        for name in dir(XK):
            if name[:3] == "XK_" and getattr(XK, name) == keysym:
                return name[3:]
        return "[%d]" % keysym

    def record_callback(self, reply):
        if reply.category != record.FromServer:
            return
        if reply.client_swapped:
            print("* received swapped protocol data, cowardly ignored")
            return
        if not len(reply.data) or reply.data[0] < 2:
            # not an event
            return

        data = reply.data
        while len(data):
            event, data = rq.EventField(None).parse_binary_value(data, self.record_dpy.display, None, None)

            if event.type in [X.KeyPress, X.KeyRelease]:
                keysym = self.local_dpy.keycode_to_keysym(event.detail, 0)
                keystr = self.lookup_keysym(keysym)

                if event.type == X.KeyPress:
                    self.pressed_keys.add(keystr)
                    for (mod, key), action in self.key_combinations.items():
                        if mod in self.pressed_keys and key == keystr:
                            action()
                elif event.type == X.KeyRelease:
                    if keystr in self.pressed_keys:
                        self.pressed_keys.remove(keystr)

                # if keysym == XK.XK_Escape:  # Example exit condition
                #     self.local_dpy.record_disable_context(ctx)
                #     self.local_dpy.flush()
                #     return

    def start(self):
        if not self.record_dpy.has_extension("RECORD"):
            print("RECORD extension not found")
            sys.exit(1)

        ctx = self.record_dpy.record_create_context(
            0,
            [record.AllClients],
            [{
                'core_requests': (0, 0),
                'core_replies': (0, 0),
                'ext_requests': (0, 0, 0, 0),
                'ext_replies': (0, 0, 0, 0),
                'delivered_events': (0, 0),
                'device_events': (X.KeyPress, X.KeyRelease),
                'errors': (0, 0),
                'client_started': False,
                'client_died': False,
            }]
        )
        self.record_dpy.record_enable_context(ctx, self.record_callback)
        self.record_dpy.record_free_context(ctx)

# Example usage
if __name__ == "__main__":
    def on_super_left():
        print("Super+Left pressed!")

    local_dpy = display.Display()
    record_dpy = display.Display()

    key_combinations = {
        ('Super_L', 'Left'): on_super_left,
        # Add more combinations and callbacks as needed
    }

    monitor = KeyMonitor(local_dpy, record_dpy, key_combinations, None)
    monitor.start()
