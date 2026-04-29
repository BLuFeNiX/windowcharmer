"""Unit tests for InputManager (XI2-based input event loop).

The manager glues together XI2 device enumeration, passive grabs, sourceid
filtering, and event dispatch. Tests below mock the X server interactions
and exercise the dispatch logic directly.
"""

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from Xlib import X
from Xlib.ext import xinput
from Xlib.ext.ge import GenericEventCode

from windowcharmer.input.xi2_manager import (
    InputManager,
    InputManagerError,
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
    # XIPassiveGrabDevice reply: empty .modifiers means all combos grabbed cleanly.
    dpy.screen.return_value.root.xinput_grab_keycode.return_value = SimpleNamespace(modifiers=[])
    return dpy


def _xi2_key_event(
    evtype: int,
    keycode: int,
    sourceid: int,
    deviceid: int | None = None,
    effective_mods: int = 0,
) -> MagicMock:
    """Synthesize an XI2 GenericEvent for any KeyPress/KeyRelease/RawKey* evtype.

    For raw events, deviceid defaults to sourceid (slave-originated, what
    we keep). Pass an explicit deviceid != sourceid to simulate a master
    echo (which the manager filters out).

    ``effective_mods`` is the modifier bitmask the X server reports on
    the event; the dispatcher uses its Shift bit to pick between the
    Mod4 and Mod4+Shift keycode tables. Raw events don't go through
    the dispatcher so ``mods`` only matters for KeyPress events.
    """
    event = MagicMock()
    event.type = GenericEventCode
    event.evtype = evtype
    if deviceid is None:
        deviceid = sourceid
    event.data = SimpleNamespace(
        detail=keycode,
        sourceid=sourceid,
        deviceid=deviceid,
        time=12345,
        mods=SimpleNamespace(effective_mods=effective_mods),
    )
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


def test_xtest_raw_keypress_is_dropped_before_reaching_tracker() -> None:
    """Raw events whose sourceid is in the XTEST set must not reach the
    tracker — they're our own xtest.fake_input output coming back through
    the raw event stream."""
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})

    mgr._handle_event(_xi2_key_event(xinput.RawKeyPress, keycode=133, sourceid=_XTEST_KBD_ID))
    mgr._handle_event(_xi2_key_event(xinput.RawKeyRelease, keycode=133, sourceid=_XTEST_KBD_ID))

    tracker.handle_event.assert_not_called()


def test_xtest_grabbed_keypress_is_dropped_before_dispatch() -> None:
    """If a synthetic event somehow matches a grabbed keycode (xtest can
    fake any chord), still don't dispatch — we only want real user input
    to trigger tile actions."""
    action = MagicMock()
    mgr, _, _, _, _ = _make_manager({"Up": action})
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: action}

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=100, sourceid=_XTEST_KBD_ID))

    action.assert_not_called()


def test_real_keypress_at_grabbed_keycode_invokes_action() -> None:
    action = MagicMock()
    mgr, _, tracker, _, _ = _make_manager({"Up": action})
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: action}

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=100, sourceid=_REAL_KBD_ID))

    # The chord time (XI2 data.time) is forwarded to the action callback so
    # downstream EWMH activate messages carry a recent server timestamp.
    action.assert_called_once_with(12345)
    tracker.handle_event.assert_not_called()  # regular KeyPress doesn't go to tracker


def test_shift_chord_dispatches_to_shift_table() -> None:
    """A KeyPress with the Shift bit set in effective_mods looks up the
    keycode in ``_shift_keycode_actions``, not the plain ``_keycode_actions``.
    This is how Super+Shift+Left gets routed to FOCUS_LEFT instead of
    Super+Left's LEFT action sharing the same keycode."""
    plain_action = MagicMock()
    shift_action = MagicMock()
    mgr, _, _, _, _ = _make_manager()
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: plain_action}
    mgr._shift_keycode_actions = {100: shift_action}

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=100, sourceid=_REAL_KBD_ID, effective_mods=X.ShiftMask))

    shift_action.assert_called_once_with(12345)
    plain_action.assert_not_called()


def test_no_shift_chord_dispatches_to_plain_table() -> None:
    """The inverse: an event with no Shift bit hits the plain table even
    when the same keycode is also registered in the shift table."""
    plain_action = MagicMock()
    shift_action = MagicMock()
    mgr, _, _, _, _ = _make_manager()
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})
    mgr._keycode_actions = {100: plain_action}
    mgr._shift_keycode_actions = {100: shift_action}

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=100, sourceid=_REAL_KBD_ID, effective_mods=0))

    plain_action.assert_called_once_with(12345)
    shift_action.assert_not_called()


def test_real_raw_keypress_reaches_tracker() -> None:
    """Raw events fire before focus/grab dispatch — they're how we see bare
    Super presses that wouldn't otherwise be delivered to our window."""
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset({_XTEST_KBD_ID})

    event = _xi2_key_event(xinput.RawKeyPress, keycode=133, sourceid=_REAL_KBD_ID)
    mgr._handle_event(event)

    tracker.handle_event.assert_called_once_with(X.KeyPress, 133)


def test_real_raw_keyrelease_reaches_tracker_with_release_type() -> None:
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset()

    event = _xi2_key_event(xinput.RawKeyRelease, keycode=133, sourceid=_REAL_KBD_ID)
    mgr._handle_event(event)

    tracker.handle_event.assert_called_once_with(X.KeyRelease, 133)


def test_raw_master_echo_is_dropped() -> None:
    """A single physical press generates raw events from both the slave
    (deviceid=sourceid) AND its master (deviceid=master, sourceid=slave).
    The master echo must be filtered so the tracker sees one event per
    press, not two."""
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset()

    master_id = 3
    slave_id = _REAL_KBD_ID

    # Slave-originated event: keep.
    slave_event = _xi2_key_event(xinput.RawKeyPress, keycode=133, sourceid=slave_id, deviceid=slave_id)
    # Master echo of the same press: drop.
    master_echo = _xi2_key_event(xinput.RawKeyPress, keycode=133, sourceid=slave_id, deviceid=master_id)

    mgr._handle_event(slave_event)
    mgr._handle_event(master_echo)

    assert tracker.handle_event.call_count == 1


def test_regular_keypress_at_non_grabbed_keycode_does_not_reach_tracker() -> None:
    """Regular XI KeyPress only fires from passive grab activation — so a
    KeyPress at an ungrabbed keycode shouldn't have arrived in practice,
    but if it does (e.g. focus on root), the tracker should still ignore
    it. The tracker is fed exclusively by raw events."""
    mgr, _, tracker, _, _ = _make_manager()
    mgr._xtest_devices = frozenset()
    mgr._keycode_actions = {100: MagicMock()}  # 133 is NOT grabbed

    mgr._handle_event(_xi2_key_event(xinput.KeyPress, keycode=133, sourceid=_REAL_KBD_ID))

    tracker.handle_event.assert_not_called()


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

    with pytest.raises(InputManagerError, match="XInputExtension"):
        mgr.start()


# ---------------------------------------------------------------------------
# Grab failure surfacing — XIPassiveGrabDevice silently rejects per-modifier
# combos when another client already holds the chord. Surface as warnings.
# ---------------------------------------------------------------------------


def test_grab_warns_when_passive_grab_partially_fails(caplog: pytest.LogCaptureFixture) -> None:
    """Reply.modifiers lists the combos that failed. Non-empty → warning."""
    import logging as stdlib_logging

    actions = {"space": MagicMock()}
    mgr, _, _, _, _ = _make_manager(actions)
    root = mgr.dpy.screen.return_value.root
    failed_entry = SimpleNamespace(modifiers=0x40, status=X.AlreadyGrabbed)  # 0x40 = Mod4Mask
    root.xinput_grab_keycode.return_value = SimpleNamespace(modifiers=[failed_entry])

    caplog.set_level(stdlib_logging.WARNING, logger="windowcharmer.input.xi2_manager")
    mgr._grab_tile_keys(root)

    matches = [r for r in caplog.records if "Could not grab" in r.message]
    assert len(matches) == 1
    assert "space" in matches[0].message


def test_grab_silent_when_passive_grab_fully_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    """Empty reply.modifiers → no warning."""
    import logging as stdlib_logging

    actions = {"space": MagicMock()}
    mgr, _, _, _, _ = _make_manager(actions)
    root = mgr.dpy.screen.return_value.root
    # _make_dpy already sets the success default, but be explicit here.
    root.xinput_grab_keycode.return_value = SimpleNamespace(modifiers=[])

    caplog.set_level(stdlib_logging.WARNING, logger="windowcharmer.input.xi2_manager")
    mgr._grab_tile_keys(root)

    grab_warnings = [r for r in caplog.records if "Could not grab" in r.message]
    assert grab_warnings == []


# ---------------------------------------------------------------------------
# Generic event dispatch — non-XI2 GenericEvents (theoretically possible from
# other extensions) must not crash the manager.
# ---------------------------------------------------------------------------


def test_start_selects_hierarchy_on_all_devices_not_all_master_devices() -> None:
    """Regression: HierarchyChanged must be selected on AllDevices, not
    AllMasterDevices. The X server returns BadValue for HierarchyChangedMask
    on AllMasterDevices — and python-xlib's randr.py registers BadRRModeError
    at the same absolute error code 2 as core BadValue, mis-classifying the
    BadValue as a malformed BadRRModeError that crashes the parser. Splitting
    the select into per-deviceid entries avoids the trigger entirely.
    """
    actions: dict[str, MagicMock] = {}  # no actions → grab loop is a no-op
    mgr, dpy, _, _, _ = _make_manager(actions)

    # Make _run_loop exit immediately so start() returns.
    dpy.pending_events.return_value = 0
    mgr._stopped = True

    with (
        patch("windowcharmer.input.xi2_manager.select.select", return_value=([], [], [])),
        contextlib.suppress(Exception),  # don't care about loop exit details
    ):
        mgr.start()

    root = dpy.screen.return_value.root
    root.xinput_select_events.assert_called_once()
    masks = root.xinput_select_events.call_args.args[0]
    by_device = {entry[0]: entry[1] for entry in masks}

    assert xinput.AllDevices in by_device, "events must be selected on AllDevices"
    assert by_device[xinput.AllDevices] & xinput.HierarchyChangedMask
    assert by_device[xinput.AllDevices] & xinput.RawKeyPressMask
    assert by_device[xinput.AllDevices] & xinput.RawKeyReleaseMask
    # Regular KeyPress/Release must NOT be selected on root — passive grab
    # delivers chord events on its own, and selecting on root would cause
    # those events to arrive twice (once via grab, once via select).
    for _scope, mask in by_device.items():
        assert not (mask & xinput.KeyPressMask), (
            f"Regular KeyPress should not be in select_events; found in scope {_scope}"
        )


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
