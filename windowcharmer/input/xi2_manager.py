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


# Wire layout of XIRawKeyEvent's fixed prefix (the bytes after the 10-byte
# GenericEvent header). python-xlib's xinput.py registers a parser for the
# regular KeyPress/KeyRelease (DeviceEventData) but not for raw events, so
# we add one for the fields we actually need: deviceid + sourceid (XTEST
# filtering) and detail (keycode for the passthrough tracker). Trailing
# valuator data is ignored — we don't care about pointer-axis values.
_RAW_DEVICE_EVENT_DATA = rq.Struct(
    rq.Card16("deviceid"),
    rq.Card32("time"),
    rq.Card32("detail"),
    rq.Card16("sourceid"),
    rq.Card16("valuators_len"),
    rq.Card32("flags"),
)


def _register_raw_event_parser(dpy: Display) -> None:
    """Tell python-xlib how to parse XI2 RawKeyPress / RawKeyRelease events.

    Without this, dpy.next_event() returns a GenericEvent whose .data is
    None for raw events, leaving us unable to read the keycode or sourceid.
    """
    extension = dpy.query_extension("XInputExtension")
    if extension is None:
        return
    opcode = extension.major_opcode
    dpy.ge_add_event_data(opcode, xinput.RawKeyPress, _RAW_DEVICE_EVENT_DATA)
    dpy.ge_add_event_data(opcode, xinput.RawKeyRelease, _RAW_DEVICE_EVENT_DATA)


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
        # python-xlib hardcodes XI 2.0 — that has KeyPress/Release,
        # RawKeyPress/Release, HierarchyChanged, and PassiveGrabDevice.
        self.dpy.xinput_query_version()
        _register_raw_event_parser(self.dpy)

        self._xtest_devices = _scan_xtest_devices(self.dpy)
        logger.debug("XTEST device IDs: %s", sorted(self._xtest_devices))

        root = self.dpy.screen().root
        try:
            # Three different event-mask scopes:
            #   - AllMasterDevices, KeyPress/Release: regular events from the
            #     master keyboard. These arrive when a passive grab fires
            #     (Super+Up etc) — that's how tile chords reach us.
            #   - AllDevices, RawKeyPress/Release: every key event from
            #     every device, fired BEFORE focus/grab dispatch. This is
            #     what feeds the bare-Super-tap detector — without raw
            #     events we'd only see keys when the daemon's window was
            #     focused (never, in practice).
            #   - AllDevices, HierarchyChanged: device-independent event
            #     for hot-plug. The X server REJECTS this mask on
            #     AllMasterDevices with a BadValue, which is why the masks
            #     get split by deviceid scope.
            root.xinput_select_events(
                [
                    (xinput.AllMasterDevices, xinput.KeyPressMask | xinput.KeyReleaseMask),
                    (
                        xinput.AllDevices,
                        xinput.RawKeyPressMask | xinput.RawKeyReleaseMask | xinput.HierarchyChangedMask,
                    ),
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
        if evtype == xinput.KeyPress:
            self._handle_grabbed_chord(event.data)
        elif evtype in (xinput.RawKeyPress, xinput.RawKeyRelease):
            self._handle_raw_key_event(evtype, event.data)
        elif evtype == xinput.HierarchyChanged:
            self._handle_hierarchy_change(event.data)

    def _handle_grabbed_chord(self, data: Any) -> None:
        """Regular XI KeyPress arrives only when our passive grab fires —
        tile chord match by keycode and dispatch."""
        # Synthetic events shouldn't reach us via passive grab (xtest goes
        # through master like real input, but our grab modifier requirement
        # filters most synthesis), but check anyway for safety.
        if data.sourceid in self._xtest_devices:
            return
        if data.detail in self._keycode_actions:
            self._keycode_actions[data.detail]()

    def _handle_raw_key_event(self, evtype: int, data: Any) -> None:
        """Raw events fire before focus/grab dispatch — every key on every
        device, regardless of which window is focused. This is what feeds
        the bare-Super-tap detector. Filter our own xtest injections by
        sourceid so simulate_hyper_press's output doesn't loop back."""
        if data.sourceid in self._xtest_devices:
            return
        core_type = X.KeyPress if evtype == xinput.RawKeyPress else X.KeyRelease
        self.passthrough_tracker.handle_event(_CoreKeyEventAdapter(core_type, data.detail))

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
    object is irrelevant to it. We adapt XI2 raw key events into that
    minimal shape so the tracker can stay protocol-agnostic.
    """

    __slots__ = ("detail", "type")

    def __init__(self, core_type: int, keycode: int) -> None:
        self.type = core_type
        self.detail = keycode
