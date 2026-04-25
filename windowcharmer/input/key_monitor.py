from collections.abc import Callable
from typing import Any
from Xlib import X, XK
from Xlib.ext import record
from Xlib.display import Display
from Xlib.protocol import rq
import logging

logger = logging.getLogger(__name__)

def get_keycode(dpy: Display, keystring: str) -> int:
    code = dpy.keysym_to_keycode(XK.string_to_keysym(keystring))
    return int(code)

class KeyMonitor:
    def __init__(self, dpy: Display, callback: Callable[[Any], None]) -> None:
        self.dpy = dpy
        self.callback = callback

    def start(self) -> None:
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
                'device_events': (X.KeyPress, X.KeyRelease, X.MappingNotify),
                'errors': (0, 0),
                'client_started': False,
                'client_died': False,
            }]
        )

        def inner_callback(reply: Any) -> None:
            if reply.category != record.FromServer:
                return
            if reply.client_swapped:
                logger.warning("* received swapped protocol data, cowardly ignored")
                return
            if not len(reply.data) or reply.data[0] < 2:
                # not an event
                return

            data = reply.data
            while len(data):
                event, data = rq.EventField('event').parse_binary_value(data, self.dpy.display, None, None)
                if event.type == X.MappingNotify:
                    # Update Xlib's internal mapping so keysym_to_keycode works correctly
                    self.dpy.refresh_keyboard_mapping(event)
                
                self.callback(event)

        self.dpy.record_enable_context(ctx, inner_callback)
        self.dpy.record_free_context(ctx)
