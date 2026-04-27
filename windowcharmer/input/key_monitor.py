import logging
from collections.abc import Callable
from typing import Any

from Xlib import X
from Xlib.display import Display
from Xlib.ext import record
from Xlib.protocol import rq

logger = logging.getLogger(__name__)


class KeyMonitor:
    def __init__(self, dpy: Display, callback: Callable[[rq.Event], None]) -> None:
        self.dpy = dpy
        self.callback = callback
        # python-xlib's record context handle has no exported type; treat it
        # as an opaque object passed back to record_disable_context/free.
        self.ctx: object | None = None

    def start(self) -> None:
        if not self.dpy.has_extension("RECORD"):
            raise OSError("RECORD extension not found.")

        self.ctx = self.dpy.record_create_context(
            0,
            [record.AllClients],
            [
                {
                    "core_requests": (0, 0),
                    "core_replies": (0, 0),
                    "ext_requests": (0, 0, 0, 0),
                    "ext_replies": (0, 0, 0, 0),
                    "delivered_events": (0, 0),
                    "device_events": (X.KeyPress, X.KeyRelease, X.MappingNotify),
                    "errors": (0, 0),
                    "client_started": False,
                    "client_died": False,
                }
            ],
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
            while data:
                event, data = rq.EventField("event").parse_binary_value(data, self.dpy.display, None, None)
                if event.type == X.MappingNotify:
                    # Update Xlib's internal mapping so keysym_to_keycode works correctly
                    self.dpy.refresh_keyboard_mapping(event)

                self.callback(event)

        try:
            self.dpy.record_enable_context(self.ctx, inner_callback)
        finally:
            self.dpy.record_free_context(self.ctx)
            # Null the handle so a second stop() call won't try to disable a
            # freed context.
            self.ctx = None

    def stop(self) -> None:
        # Snapshot the context handle before any blocking call so a concurrent
        # start() finalize that nulls self.ctx can't leave us calling
        # record_disable_context with None partway through.
        ctx = self.ctx
        if ctx is None:
            return
        # record_disable_context must be called from a *different* Display connection
        # than record_enable_context — python-xlib is not thread-safe on a single Display.
        # A throwaway connection is used (not DisplayPool) so it can be closed immediately.
        try:
            stop_dpy = Display()
        except Exception as e:
            logger.debug("KeyMonitor.stop: failed to open display: %s", e)
            return
        try:
            stop_dpy.record_disable_context(ctx)
            stop_dpy.flush()
        except Exception as e:
            logger.debug("KeyMonitor.stop: failed to disable record context: %s", e)
        finally:
            stop_dpy.close()
