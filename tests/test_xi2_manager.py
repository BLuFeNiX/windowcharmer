"""Unit tests for InputManager (XI2-based input event loop).

The manager glues together XI2 device enumeration, passive grabs, sourceid
filtering, and event dispatch. Tests below mock the X server interactions
and exercise the dispatch logic directly.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from Xlib import X
from Xlib.ext import xinput
from Xlib.ext.ge import GenericEventCode

from windowcharmer.input.xi2_manager import (
    InputManager,
    InputManagerError,
    _CoreKeyEventAdapter,
    _scan_xtest_devices,
)

_XTEST_KBD_ID = 5
_XTEST_PTR_ID = 4
_REAL_KBD_ID = 14


def _make_dpy(xtest_atom: int = 289) -> MagicMock:
    """Return a Display mock wired for the XI2 calls InputManager makes.

    Devices: master kbd (id=3), XTEST kbd (id=5), real kbd (id=14). The
    XTEST device alone has the XTEST Device property; real ones don't.
    """
    dpy = MagicMock()

    devices = SimpleNamespace(
        devices=[
            SimpleNamespace(deviceid=3, name="Virtual core keyboard", use=xinput.MasterKeyboard),
            SimpleNamespace(deviceid=4, name="Virtual core XTEST pointer", use=xinput.SlavePointer),
            SimpleNamespace(deviceid=5, name="Virtual core XTEST keyboard", use=xinput.SlaveKeyboard),
            SimpleNamespace(deviceid=14, name="Keychron K10", use=xinput.SlaveKeyboard),
        ],
    )
    dpy.xinput_query_device.return_value = devices

    # Property listings: XTEST devices have the atom, others don't.
    def _list_props(deviceid: int) -> SimpleNamespace:
        if deviceid in (_XTEST_KBD_ID, _XTEST_PTR_ID):
            return SimpleNamespace(atoms=[xtest_atom])
        return SimpleNamespace(atoms=[])

    dpy.xinput_list_device_properties.side_effect = _list_props
    dpy.intern_atom.return_value = xtest_atom

    dpy.has_extension.return_value = True
    dpy.keysym_to_keycode.return_value = 100  # any non-zero
    return dpy


def _xi2_key_event(evtype: int, keycode: int, sourceid: int) -> MagicMock:
    """Synthesize an XI2 GenericEvent for KeyPress/KeyRelease."""
    event = MagicMock()
    event.type = GenericEventCode
    event.evtype = evtype
    event.data = SimpleNamespace(detail=keycode, sourceid=sourceid)
    return event


def _hierarchy_event(flags: int) -> MagicMock:
    event = MagicMock()
    event.type = GenericEventCode
    event.evtype = xinput.HierarchyChanged
    event.data = SimpleNamespace(flags=flags)
    return event


def _mapping_notify(request: int = X.MappingKeyboard) -> MagicMock:
    event = MagicMock()
    event.type = X.MappingNotify
    event.request = request
    return event


def _make_manager(actions: dict | None = None) -> tuple[InputManager, MagicMock, MagicMock, MagicMock, MagicMock]:
    actions = actions or {"Up": MagicMock()}
    dpy = _make_dpy()
    tracker = MagicMock()
    on_keymap_change = MagicMock()
    on_keyboard_hotplug = MagicMock()
    mgr = InputManager(
        dpy,
        key_actions=actions,
        passthrough_tracker=tracker,
        on_keymap_change=on_keymap_change,
        on_keyboard_hotplug=on_keyboard_hotplug,
    )
    return mgr, dpy, tracker, on_keymap_change, on_keyboard_hotplug


# ---------------------------------------------------------------------------
# _scan_xtest_devices — pure function, the canonical XTEST detection path.
# ---------------------------------------------------------------------------


def test_scan_finds_xtest_devices_via_property() -> None:
    dpy = _make_dpy()
    ids = _scan_xtest_devices(dpy)
    assert ids == frozenset({_XTEST_KBD_ID, _XTEST_PTR_ID})


def test_scan_returns_empty_if_no_devices_have_xtest_property() -> None:
    dpy = _make_dpy()
    dpy.xinput_list_device_properties.side_effect = lambda did: SimpleNamespace(atoms=[])
    assert _scan_xtest_devices(dpy) == frozenset()


def test_scan_skips_devices_that_reject_property_query() -> None:
    """Some virtual devices error on property queries — those clearly aren't
    XTEST (which always has the property), so swallow and continue."""
    dpy = _make_dpy()

    def _list_props(deviceid: int) -> SimpleNamespace:
        if deviceid == 3:
            raise Exception("BadDevice")
        if deviceid in (_XTEST_KBD_ID, _XTEST_PTR_ID):
            return SimpleNamespace(atoms=[289])
        return SimpleNamespace(atoms=[])

    dpy.xinput_list_device_properties.side_effect = _list_props
    ids = _scan_xtest_devices(dpy)
    assert ids == frozenset({_XTEST_KBD_ID, _XTEST_PTR_ID})


# ---------------------------------------------------------------------------
# Synthetic-event filtering — the headline reason we moved to XI2.
# ---------------------------------------------------------------------------


def test_xtest_keypress_is_dropped_before_reaching_tracker_or_actions() -> None:
    """Events whose sourceid is in the XTEST set must not reach the tracker
    or trigger a tile action — they're our own xtest.fake_input output coming
    back through the event stream."""
    action = MagicMock()
    mgr, _, tracker, _, _ = _make_manager({"Up": action})
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: action}

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=100, sourceid=_XTEST_KBD_ID))
    mgr._handle_event(_xi2_key_event(xinput.KeyRelease, keycode=100, sourceid=_XTEST_KBD_ID))

    action.assert_not_called()
    tracker.handle_event.assert_not_called()


def test_real_keypress_at_grabbed_keycode_invokes_action() -> None:
    action = MagicMock()
    mgr, _, tracker, _, _ = _make_manager({"Up": action})
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: action}

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=100, sourceid=_REAL_KBD_ID))

    action.assert_called_once()
    tracker.handle_event.assert_not_called()  # action consumed it; not for tracker


def test_real_keypress_at_non_grabbed_keycode_reaches_tracker() -> None:
    """Non-tile keys (Super press, alpha keys, etc.) flow through to the
    bare-Super-tap tracker so it can detect press/release patterns."""
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: MagicMock()}

    event = _xi2_key_event(xinput.KeyPress, keycode=133, sourceid=_REAL_KBD_ID)
    mgr._handle_event(event)

    tracker.handle_event.assert_called_once()
    forwarded = tracker.handle_event.call_args.args[0]
    assert isinstance(forwarded, _CoreKeyEventAdapter)
    assert forwarded.type == X.KeyPress
    assert forwarded.detail == 133


def test_real_keyrelease_reaches_tracker_with_release_type() -> None:
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset()

    event = _xi2_key_event(xinput.KeyRelease, keycode=133, sourceid=_REAL_KBD_ID)
    mgr._handle_event(event)

    forwarded = tracker.handle_event.call_args.args[0]
    assert forwarded.type == X.KeyRelease
    assert forwarded.detail == 133


# ---------------------------------------------------------------------------
# Hot-plug — HierarchyChanged routes through the same event loop.
# ---------------------------------------------------------------------------


def test_hierarchy_change_with_slave_added_triggers_hotplug_and_rescan() -> None:
    mgr, _, _, _, on_hotplug = _make_manager()
    mgr._xtest_devices = frozenset()

    mgr._handle_event(_hierarchy_event(flags=xinput.SlaveAdded))

    on_hotplug.assert_called_once()
    # Re-enumeration ran — the user's stub returns id 5 + 4 as XTEST devices.
    assert mgr._xtest_devices == frozenset({_XTEST_KBD_ID, _XTEST_PTR_ID})


def test_hierarchy_change_with_master_added_re_enumerates_xtest() -> None:
    """MPX: a new master pair creates new XTEST slave devices. We must catch
    those in the filter set, otherwise xtest events from the new master
    would leak through."""
    mgr, _, _, _, on_hotplug = _make_manager()
    mgr._xtest_devices = frozenset()

    mgr._handle_event(_hierarchy_event(flags=xinput.MasterAdded))

    on_hotplug.assert_called_once()
    assert mgr._xtest_devices == frozenset({_XTEST_KBD_ID, _XTEST_PTR_ID})


def test_hierarchy_change_without_relevant_flags_is_ignored() -> None:
    """Removed devices, slave-detached events, etc. shouldn't trigger a rebind
    storm — we only act when something arrives or comes online."""
    mgr, _, _, _, on_hotplug = _make_manager()
    initial = frozenset({99})  # something that won't match the stub
    mgr._xtest_devices = initial

    mgr._handle_event(_hierarchy_event(flags=xinput.SlaveRemoved))

    on_hotplug.assert_not_called()
    assert mgr._xtest_devices == initial  # untouched


# ---------------------------------------------------------------------------
# MappingNotify — core X event still flows through, triggers re-grab.
# ---------------------------------------------------------------------------


def test_mapping_notify_keyboard_calls_keymap_change_and_re_grabs() -> None:
    mgr, dpy, _, on_keymap, _ = _make_manager()
    mgr._keycode_actions = {100: MagicMock()}

    mgr._handle_event(_mapping_notify(X.MappingKeyboard))

    on_keymap.assert_called_once()
    # Re-grab: ungrab + re-grab cycle should have hit the root window methods.
    root = dpy.screen.return_value.root
    assert root.xinput_ungrab_keycode.called
    assert root.xinput_grab_keycode.called


def test_mapping_notify_pointer_does_not_trigger_keymap_change() -> None:
    """Pointer mapping changes can't affect Super_L position — don't burn a
    keymap rebind cycle on them."""
    mgr, _, _, on_keymap, _ = _make_manager()

    mgr._handle_event(_mapping_notify(X.MappingPointer))

    on_keymap.assert_not_called()


# ---------------------------------------------------------------------------
# stop() — signal-safe exit path.
# ---------------------------------------------------------------------------


def test_stop_sets_flag_and_writes_wake_pipe() -> None:
    """The flag flips immediately and the wake pipe gets a non-blocking byte
    so a parked next_event() returns. Multiple stop() calls must be safe."""
    mgr, _, _, _, _ = _make_manager()
    mgr._wake_w = -1  # not yet started; should still be safe to call

    mgr.stop()
    assert mgr._stopped is True

    # Now simulate started state with a real pipe-ish fd.
    with patch("windowcharmer.input.xi2_manager.os.write") as wr:
        mgr._wake_w = 42
        mgr.stop()
        wr.assert_called_once_with(42, b"x")


# ---------------------------------------------------------------------------
# Startup error path — XI2 missing should fail fast.
# ---------------------------------------------------------------------------


def test_start_raises_input_manager_error_when_xi2_missing() -> None:
    mgr, dpy, _, _, _ = _make_manager()
    dpy.has_extension.return_value = False

    try:
        mgr.start()
    except InputManagerError as e:
        assert "XInputExtension" in str(e)
    else:
        raise AssertionError("expected InputManagerError")


# ---------------------------------------------------------------------------
# Generic event dispatch — non-XI2 GenericEvents (theoretically possible from
# other extensions) must not crash the manager.
# ---------------------------------------------------------------------------


def test_unknown_xi2_evtype_is_silently_ignored() -> None:
    mgr, _, tracker, _, on_hotplug = _make_manager()
    event = MagicMock()
    event.type = GenericEventCode
    event.evtype = 999  # not KeyPress/Release/HierarchyChanged
    event.data = SimpleNamespace()

    mgr._handle_event(event)  # must not raise

    tracker.handle_event.assert_not_called()
    on_hotplug.assert_not_called()


def test_non_generic_non_mapping_events_are_ignored() -> None:
    """A core event we don't care about (e.g. PropertyNotify) must not reach
    the tracker or trigger callbacks."""
    mgr, _, tracker, on_keymap, on_hotplug = _make_manager()
    event = MagicMock()
    event.type = X.PropertyNotify

    mgr._handle_event(event)

    tracker.handle_event.assert_not_called()
    on_keymap.assert_not_called()
    on_hotplug.assert_not_called()
