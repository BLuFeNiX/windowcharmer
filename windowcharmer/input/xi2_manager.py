"""Unified XI2-based input layer.

Replaces the trio of:
  - KeyGrabber (XGrabKey + select() loop)
  - KeyMonitor (XRecord on a separate Display + thread)
  - UdevKeyboardMonitor (pyudev hot-plug + thread)

Why XI2:

  1. **sourceid** lets us filter our own xtest-injected events at the API
     level — XI2 events carry the deviceid that produced them, and XTEST
     virtual devices can be identified at startup by the canonical
     XI_PROP_XTEST_DEVICE property (xserver-properties.h). This eliminates
     the synthetic-event feedback-loop hazard the SuperPassthroughTracker
     used to guard against with a count-based suppression hack.

  2. **HierarchyChanged** delivers keyboard-hot-plug events over the same X
     connection as everything else, removing the pyudev dependency and the
     separate netlink-poll thread.

  3. **XIPassiveGrabDevice** accepts a list of modifier combinations in a
     single call, replacing the per-lock-combo XGrabKey loop.

Single thread, single Display, three responsibilities collapsed into one
event loop. Core MappingNotify still arrives over the same connection
(XI2 doesn't displace core events), so the keymap-rebind path is unchanged.
"""

import contextlib
import logging
import os
import select
from collections.abc import Callable
from typing import Any

from Xlib import XK, X
from Xlib.display import Display
from Xlib.error import BadAccess
from Xlib.ext import xinput
from Xlib.ext.ge import GenericEventCode
from Xlib.protocol import rq
from Xlib.xobject.drawable import Window

from .super_passthrough import SuperPassthroughTracker

logger = logging.getLogger(__name__)


class InputManagerError(Exception):
    """Raised when the input manager cannot start (XI2 missing, BadAccess, etc.)."""


# Lock-state modifier combinations to grab alongside the base modifier so the
# chord matches regardless of NumLock / CapsLock state.
_IGNORED_LOCKS: tuple[int, ...] = (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask)


def _scan_xtest_devices(dpy: Display) -> frozenset[int]:
    """Return XI deviceids of all devices marked as XTEST.

    Identified via the XI_PROP_XTEST_DEVICE property (defined in xserver-
    properties.h, attached read-only by the X server to every XTEST slave).
    Name-matching ('Virtual core XTEST keyboard') would also work on stock
    Xorg/Xwayland but is fragile under custom MPX setups; the property is
    the actual server contract.

    Each master pointer/keyboard pair gets its own XTEST slave pair, so on
    multi-pointer setups this set can contain more than two ids — and a new
    master created at runtime adds new ones, which is why HierarchyChanged
    re-runs this scan.
    """
    xtest_atom = dpy.intern_atom("XTEST Device")
    result: set[int] = set()
    devices = dpy.xinput_query_device(xinput.AllDevices).devices
    for d in devices:
        try:
            props = dpy.xinput_list_device_properties(d.deviceid)
        except Exception:
            # Some devices reject property queries; not XTEST by definition.
            continue
        if xtest_atom in props.atoms:
            result.add(d.deviceid)
    return frozenset(result)


class InputManager:
    """XI2-based input event loop.

    Subscribes on the root window to KeyPress / KeyRelease (for the
    passthrough tracker and tile-key dispatch) plus HierarchyChanged (for
    keyboard hot-plug). Tile keys are passive-grabbed via XIPassiveGrabDevice
    with all four lock-state combinations passed in a single call.
    """

    def __init__(
        self,
        dpy: Display,
        key_actions: dict[str, Callable[[], None]],
        passthrough_tracker: SuperPassthroughTracker,
        on_keymap_change: Callable[[], None],
        on_keyboard_hotplug: Callable[[], None],
        modifier: int = X.Mod4Mask,
    ) -> None:
        self.dpy = dpy
        self.key_actions = key_actions
        self.passthrough_tracker = passthrough_tracker
        self.on_keymap_change = on_keymap_change
        self.on_keyboard_hotplug = on_keyboard_hotplug
        self.modifier = modifier

        # keycode -> action callback, populated after grabs are placed.
        self._keycode_actions: dict[int, Callable[[], None]] = {}
        self._xtest_devices: frozenset[int] = frozenset()
        self._stopped = False
        self._wake_r = -1
        self._wake_w = -1

    def start(self) -> None:
        """Run the event loop until stop() is called.

        Raises InputManagerError if XI2 is unavailable, the event-mask
        select fails, or BadAccess persists during grab placement.
        Cleanup of grabs and the wake pipe is the start() finally block's
        responsibility.
        """
        if not self.dpy.has_extension("XInputExtension"):
            raise InputManagerError("XInputExtension not present on this server")
        # python-xlib hardcodes XI 2.0 — that has KeyPress/Release, HierarchyChanged,
        # and PassiveGrabDevice, all we need.
        self.dpy.xinput_query_version()

        self._xtest_devices = _scan_xtest_devices(self.dpy)
        logger.debug("XTEST device IDs: %s", sorted(self._xtest_devices))

        root = self.dpy.screen().root
        try:
            # HierarchyChanged events are device-independent (a single event
            # covers all hierarchy changes), and the X server enforces that
            # the mask be selected on AllDevices, NOT AllMasterDevices —
            # using AllMasterDevices triggers a BadValue. Key events go
            # through master devices in the normal case, so they get the
            # AllMasterDevices selection. Two entries, one call.
            root.xinput_select_events(
                [
                    (xinput.AllMasterDevices, xinput.KeyPressMask | xinput.KeyReleaseMask),
                    (xinput.AllDevices, xinput.HierarchyChangedMask),
                ]
            )
            # Force any async error from the select to surface NOW, inside
            # this try block — otherwise it queues and explodes much later
            # on the next reply-bearing request, and (because python-xlib's
            # randr.py registers BadRRModeError at the same absolute error
            # code 2 as core BadValue) it gets mis-classified as a
            # BadRRModeError that fails to expose .sequence_number.
            self.dpy.sync()
        except Exception as e:
            raise InputManagerError(f"XISelectEvents failed: {e}") from e

        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_w, False)
        try:
            try:
                self._grab_tile_keys(root)
                self._run_loop()
            except BadAccess as e:
                raise InputManagerError(f"BadAccess during grab: {e}") from e
            except Exception as e:
                raise InputManagerError(f"event loop crashed: {e}") from e
        finally:
            self._ungrab_tile_keys(root)
            os.close(self._wake_r)
            os.close(self._wake_w)
            self._wake_r = -1
            self._wake_w = -1

    def stop(self) -> None:
        """Request a clean exit. Safe to call from a signal handler — sets a
        flag and pokes the wakeup pipe so a parked next_event() returns
        immediately.
        """
        self._stopped = True
        if self._wake_w != -1:
            with contextlib.suppress(BlockingIOError, OSError):
                os.write(self._wake_w, b"x")

    def _grab_tile_keys(self, root: Window) -> None:
        """Place XI2 passive grabs on every configured chord."""
        modifiers = [self.modifier | lock for lock in _IGNORED_LOCKS]
        for key_name, action in self.key_actions.items():
            keysym = XK.string_to_keysym(key_name)
            if not keysym:
                logger.warning("Unknown key name %r — skipping grab", key_name)
                continue
            keycode = self.dpy.keysym_to_keycode(keysym)
            if not keycode:
                logger.warning("Key %r has no keycode in current keymap — skipping", key_name)
                continue
            self._keycode_actions[keycode] = action
            root.xinput_grab_keycode(
                deviceid=xinput.AllMasterDevices,
                time=X.CurrentTime,
                keycode=keycode,
                grab_mode=xinput.GrabModeAsync,
                paired_device_mode=xinput.GrabModeAsync,
                # owner_events=False so the focused window doesn't ALSO see
                # Super+Up — we want exclusive delivery to the daemon.
                owner_events=False,
                event_mask=xinput.KeyPressMask,
                modifiers=modifiers,
            )

    def _ungrab_tile_keys(self, root: Window) -> None:
        modifiers = [self.modifier | lock for lock in _IGNORED_LOCKS]
        for keycode in list(self._keycode_actions.keys()):
            try:
                root.xinput_ungrab_keycode(
                    deviceid=xinput.AllMasterDevices,
                    keycode=keycode,
                    modifiers=modifiers,
                )
            except Exception as e:
                logger.debug("Failed to ungrab keycode %d: %s", keycode, e)
        self._keycode_actions.clear()

    def _run_loop(self) -> None:
        x_fd = self.dpy.fileno()
        while not self._stopped:
            # Drain anything already buffered before parking in select().
            while self.dpy.pending_events() > 0:
                self._handle_event(self.dpy.next_event())
                if self._stopped:
                    return
            ready, _, _ = select.select([x_fd, self._wake_r], [], [])
            if self._wake_r in ready:
                with contextlib.suppress(OSError):
                    os.read(self._wake_r, 4096)

    def _handle_event(self, event: rq.Event) -> None:
        # Core MappingNotify still flows over the same connection — XI2
        # doesn't displace core events.
        if event.type == X.MappingNotify:
            self.dpy.refresh_keyboard_mapping(event)
            if event.request == X.MappingKeyboard:
                logger.debug("MappingNotify: keyboard mapping changed")
                self.on_keymap_change()
                # Keycodes for our key names may have moved — rebuild grabs.
                root = self.dpy.screen().root
                self._ungrab_tile_keys(root)
                self._grab_tile_keys(root)
            return

        # XI2 events arrive as GenericEvent (type=35); the discriminator is
        # event.evtype, and the payload is event.data.
        if event.type != GenericEventCode:
            return

        evtype = event.evtype
        if evtype in (xinput.KeyPress, xinput.KeyRelease):
            self._handle_xi_key_event(evtype, event.data)
        elif evtype == xinput.HierarchyChanged:
            self._handle_hierarchy_change(event.data)

    def _handle_xi_key_event(self, evtype: int, data: Any) -> None:
        # Filter our own xtest injections at the source — see _scan_xtest_devices.
        if data.sourceid in self._xtest_devices:
            return

        # Tile chord match? Dispatch and stop here — the focused window
        # doesn't see this event (owner_events=False on the grab).
        if evtype == xinput.KeyPress and data.detail in self._keycode_actions:
            self._keycode_actions[data.detail]()
            return

        # Otherwise it's a non-grabbed key event for the passthrough tracker.
        self.passthrough_tracker.handle_event(_CoreKeyEventAdapter(evtype, data.detail))

    def _handle_hierarchy_change(self, data: Any) -> None:
        # MasterAdded creates new XTEST slave devices; SlaveAdded / DeviceEnabled
        # mean a keyboard appeared. Either way, re-enumerate XTEST and trigger
        # a keymap rebind so newly-attached keyboards get the swap applied.
        relevant = (
            xinput.MasterAdded
            | xinput.SlaveAdded
            | xinput.SlaveAttached
            | xinput.DeviceEnabled
        )
        if data.flags & relevant:
            self._xtest_devices = _scan_xtest_devices(self.dpy)
            logger.debug("Hierarchy changed; XTEST device IDs now: %s", sorted(self._xtest_devices))
            self.on_keyboard_hotplug()


class _CoreKeyEventAdapter:
    """Duck-typed core-X-event for the SuperPassthroughTracker.

    The tracker only reads `.type` and `.detail` — the rest of an X event
    object is irrelevant to it. We adapt XI2 KeyPress/Release into that
    minimal shape so the tracker can stay protocol-agnostic.
    """

    __slots__ = ("detail", "type")

    def __init__(self, xi2_evtype: int, keycode: int) -> None:
        self.type = X.KeyPress if xi2_evtype == xinput.KeyPress else X.KeyRelease
        self.detail = keycode
