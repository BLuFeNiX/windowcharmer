"""XI2-based input event loop.

Owns three responsibilities on a single Display + thread:

  - **Tile-chord dispatch** via XIPassiveGrabDevice on Mod4+key combinations
    (one call per chord, all four lock-state masks at once).
  - **Bare-Super-tap detection** via XI2 raw events. Raw events fire before
    focus dispatch and grab activation, so the daemon sees every keystroke
    regardless of focus. Synthetic xtest output is filtered by sourceid:
    every XTEST slave is identified at startup via the canonical
    XI_PROP_XTEST_DEVICE property (xserver-properties.h) and the set is
    refreshed on every HierarchyChanged.
  - **Keyboard hot-plug** via HierarchyChanged events on the same Display.

Core MappingNotify still arrives over this connection — XI2 doesn't
displace core events — so the keymap-rebind path uses the same loop.
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
        key_actions: dict[str, Callable[[int], None]],
        passthrough_tracker: SuperPassthroughTracker,
        on_keymap_change: Callable[[], None],
        on_keyboard_hotplug: Callable[[], None],
        shift_key_actions: dict[str, Callable[[int], None]] | None = None,
    ) -> None:
        self.dpy = dpy
        self.key_actions = key_actions
        # Super+Shift+key bindings — separate table because chord matching
        # at the X11 grab level differentiates Mod4 from Mod4|Shift, and
        # we want each modifier combo to dispatch its own action.
        self.shift_key_actions = shift_key_actions or {}
        self.passthrough_tracker = passthrough_tracker
        self.on_keymap_change = on_keymap_change
        self.on_keyboard_hotplug = on_keyboard_hotplug

        # keycode -> action callback, populated after grabs are placed.
        # Callbacks receive the X server timestamp of the chord press
        # (XI2 ``data.time``); they pass it through to EWMH activate
        # messages so Mutter's focus-stealing prevention accepts the
        # request as a recent user gesture rather than dropping it.
        self._keycode_actions: dict[int, Callable[[int], None]] = {}
        # Same shape, for chords that included Shift. Lookup at dispatch
        # time is gated on the Shift bit in the event's effective mods.
        self._shift_keycode_actions: dict[int, Callable[[int], None]] = {}
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
            # All XI2 events we care about come via raw events + hot-plug —
            # both selected on AllDevices, which the X server requires for
            # HierarchyChanged anyway (it rejects HierarchyChangedMask on
            # AllMasterDevices with a BadValue). Tile chord events do NOT
            # need a regular KeyPress/Release selection on root: when a
            # passive grab activates, events are delivered to the grabbing
            # client per the grab's own event_mask, independently of any
            # XISelectEvents call. Subscribing on root anyway just delivers
            # duplicates (root is the parent of every window, so its
            # XISelectEvents covers all descendants).
            root.xinput_select_events(
                [
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
        """Place XI2 passive grabs on every configured chord.

        Grabs are placed twice: once with ``Mod4`` for the plain
        Super+key bindings and once with ``Mod4|Shift`` for the
        Super+Shift+key bindings. The X server matches modifier sets
        exactly (modulo lock-state combinations), so the two grabs are
        independent — Super+Up and Super+Shift+Up dispatch to different
        actions without interfering with each other.
        """
        self._grab_chord_table(root, X.Mod4Mask, self.key_actions, self._keycode_actions, label="Mod4")
        self._grab_chord_table(
            root,
            X.Mod4Mask | X.ShiftMask,
            self.shift_key_actions,
            self._shift_keycode_actions,
            label="Mod4+Shift",
        )

    def _grab_chord_table(
        self,
        root: Window,
        base_modifier: int,
        key_actions: dict[str, Callable[[int], None]],
        keycode_table: dict[int, Callable[[int], None]],
        label: str,
    ) -> None:
        modifiers = [base_modifier | lock for lock in _IGNORED_LOCKS]
        for key_name, action in key_actions.items():
            keysym = XK.string_to_keysym(key_name)
            if not keysym:
                logger.warning("Unknown key name %r — skipping grab", key_name)
                continue
            keycode = self.dpy.keysym_to_keycode(keysym)
            if not keycode:
                logger.warning("Key %r has no keycode in current keymap — skipping", key_name)
                continue
            keycode_table[keycode] = action
            reply = root.xinput_grab_keycode(
                deviceid=xinput.AllMasterDevices,
                time=X.CurrentTime,
                keycode=keycode,
                grab_mode=xinput.GrabModeAsync,
                paired_device_mode=xinput.GrabModeAsync,
                # owner_events=False so the focused window doesn't ALSO see
                # the chord — we want exclusive delivery to the daemon.
                owner_events=False,
                event_mask=xinput.KeyPressMask,
                modifiers=modifiers,
            )
            # XIPassiveGrabDevice replies with a list of modifier combos that
            # FAILED — empty means full success. Conflicts with another client's
            # exclusive grab (Cinnamon shortcut, IBus trigger, xkb option) show
            # up here without a synchronous BadAccess, so we surface them as
            # warnings instead of letting the chord silently never fire.
            try:
                failed = list(reply.modifiers)
            except (AttributeError, TypeError):
                failed = []
            if failed:
                logger.warning(
                    "Could not grab %s+%s (keycode=%d) — another client likely "
                    "owns this chord (Cinnamon shortcut, input-method trigger, "
                    "xkb option, etc.). Rebind in ~/.config/windowcharmer/config.toml "
                    "or unbind the conflicting client. Failed combos: %s",
                    label,
                    key_name,
                    keycode,
                    [hex(getattr(c, "modifiers", 0)) for c in failed],
                )

    def _ungrab_tile_keys(self, root: Window) -> None:
        self._ungrab_chord_table(root, X.Mod4Mask, self._keycode_actions)
        self._ungrab_chord_table(root, X.Mod4Mask | X.ShiftMask, self._shift_keycode_actions)

    def _ungrab_chord_table(self, root: Window, base_modifier: int, table: dict[int, Callable[[int], None]]) -> None:
        modifiers = [base_modifier | lock for lock in _IGNORED_LOCKS]
        for keycode in list(table.keys()):
            try:
                root.xinput_ungrab_keycode(
                    deviceid=xinput.AllMasterDevices,
                    keycode=keycode,
                    modifiers=modifiers,
                )
            except Exception as e:
                logger.debug("Failed to ungrab keycode %d: %s", keycode, e)
        table.clear()

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
        tile chord match by keycode and dispatch.

        Two grab tables exist: one for Mod4+key, one for Mod4+Shift+key.
        We pick which to look the keycode up in based on the Shift bit
        in the event's effective modifier mask. The X server only
        delivers events matching one of our registered modifier sets,
        so this is a clean shift-or-no-shift two-way split.
        """
        # Synthetic events shouldn't reach us via passive grab (xtest goes
        # through master like real input, but our grab modifier requirement
        # filters most synthesis), but check anyway for safety.
        if data.sourceid in self._xtest_devices:
            return
        shift_held = bool(data.mods.effective_mods & X.ShiftMask)
        table = self._shift_keycode_actions if shift_held else self._keycode_actions
        if data.detail in table:
            logger.debug(
                "Chord matched: keycode=%d shift=%s time=%d",
                data.detail,
                shift_held,
                data.time,
            )
            table[data.detail](data.time)

    def _handle_raw_key_event(self, evtype: int, data: Any) -> None:
        """Raw events fire before focus/grab dispatch — every key on every
        device, regardless of which window is focused. This is what feeds
        the bare-Super-tap detector.

        Two filters before forwarding to the tracker:

          - sourceid in xtest_devices: drop our own simulate_super_press
            output to break the feedback loop (otherwise each forwarded
            tap re-triggers the bare-tap detection).

          - deviceid != sourceid: drop master echoes. With AllDevices in
            the select scope, a single physical press fires raw events
            from both the originating slave (deviceid=slave, sourceid=
            slave) and its master (deviceid=master, sourceid=slave).
            We only want one event per press; keep slave-originated.
        """
        if data.sourceid in self._xtest_devices:
            return
        if data.deviceid != data.sourceid:
            return
        core_type = X.KeyPress if evtype == xinput.RawKeyPress else X.KeyRelease
        self.passthrough_tracker.handle_event(core_type, data.detail)

    def _handle_hierarchy_change(self, data: Any) -> None:
        # MasterAdded creates new XTEST slave devices; SlaveAdded / DeviceEnabled
        # mean a keyboard appeared. Either way, re-enumerate XTEST and trigger
        # a keymap rebind so newly-attached keyboards get the swap applied.
        relevant = xinput.MasterAdded | xinput.SlaveAdded | xinput.SlaveAttached | xinput.DeviceEnabled
        if data.flags & relevant:
            self._xtest_devices = _scan_xtest_devices(self.dpy)
            logger.debug("Hierarchy changed; XTEST device IDs now: %s", sorted(self._xtest_devices))
            self.on_keyboard_hotplug()
