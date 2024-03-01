from Xlib import X, XK, display
from Xlib.ext import record
from Xlib.protocol import rq
import sys
import traceback

def get_keycode(dpy, keystring):
    return dpy.keysym_to_keycode(XK.string_to_keysym(keystring))

class KeyMonitor:
    def __init__(self, dpy, callback):
        self.dpy = dpy
        self.callback = callback

    def start(self):
        if not self.dpy.has_extension("RECORD"):
            raise OSError("RECORD extension not found.")
        
        ctx = self.dpy.record_create_context(
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

        def inner_callback(reply):
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
                event, data = rq.EventField(None).parse_binary_value(data, self.dpy.display, None, None)
                self.callback(self.dpy, event)

        self.dpy.record_enable_context(ctx, inner_callback)
        self.dpy.record_free_context(ctx)


if __name__ == "__main__":
    
    dpy = display.Display()
    super_l_keycode = get_keycode(dpy, 'Super_L')

    def callback(dpy, event):
        if event.type == X.KeyPress or event.type == X.KeyRelease:
            if event.detail == super_l_keycode:
                if event.type == X.KeyPress:
                    print("Super_L key pressed")                    
                elif event.type == X.KeyRelease:
                    print("Super_L key released")

    try:
        monitor = KeyMonitor(dpy, callback)
        monitor.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    except:
        print("Unexpected error:", sys.exc_info()[0])
        traceback.print_exc()
    finally:
        dpy.flush()
