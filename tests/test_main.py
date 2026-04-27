"""Unit tests for WindowCharmerApp orchestration logic.

The app glues together five collaborators (window manager, keymap mapper,
key grabber, input services, super-tap tracker). The tests below exercise
the routing and lifecycle logic with all collaborators mocked, so the
orchestration intent is decoupled from any X11 / D-Bus availability.
"""

from unittest.mock import MagicMock, patch

from Xlib import X

from windowcharmer.config.actions import TileAction
from windowcharmer.main import WindowCharmerApp


def _make_app() -> WindowCharmerApp:
    """Construct an app with every external collaborator mocked.

    The patches are released as soon as construction finishes — instance
    attributes (app.wm, app.grabber, ...) still point at the MagicMock
    instances, so post-construction assertions still work.
    """
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper"),
        patch("windowcharmer.main.KeyGrabber"),
        patch("windowcharmer.main.InputServices"),
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        return WindowCharmerApp()


def test_do_action_routes_tile_to_window_manager() -> None:
    """Non-EXIT actions must reach the window manager, not the grabber."""
    app = _make_app()
    app.do_action(TileAction.LEFT)
    app.wm.execute_action.assert_called_once_with(TileAction.LEFT)
    app.grabber.stop.assert_not_called()


def test_do_action_exit_short_circuits_to_stop() -> None:
    """EXIT must shut down the grabber loop and never reach the window manager —
    a stray execute_action(EXIT) on the wm would not stop the daemon.
    """
    app = _make_app()
    app.do_action(TileAction.EXIT)
    app.grabber.stop.assert_called_once()
    app.wm.execute_action.assert_not_called()


def test_on_mapping_notify_dispatches_only_for_keyboard_request() -> None:
    """MappingNotify fires for keyboard, modifier, AND pointer changes; only
    keyboard requests can affect Super_L / Hyper_L positions, so non-keyboard
    variants must not trigger the swap (which would otherwise re-write the
    keymap on every pointer remap).
    """
    app = _make_app()

    app._on_mapping_notify(MagicMock(request=X.MappingPointer))
    app.mapper.apply_super_hyper_swap.assert_not_called()

    app._on_mapping_notify(MagicMock(request=X.MappingKeyboard))
    app.mapper.apply_super_hyper_swap.assert_called_once()


def test_monitor_callback_passes_refreshed_keycode_atomically() -> None:
    """Locks in the atomic-refresh contract: the keycode handed to the
    passthrough tracker must come from refresh_keycodes()'s return value, not
    from a follow-up read of self.mapper.super_l_keycode (which races a
    concurrent apply_super_hyper_swap that would mutate the field between
    refresh and read).
    """
    app = _make_app()
    app.mapper.refresh_keycodes.return_value = 999
    # super_l_keycode is deliberately a different value to detect a regression
    # to the racy "refresh; then read self.mapper.super_l_keycode" pattern.
    app.mapper.super_l_keycode = 111

    app._monitor_callback(MagicMock(type=X.MappingNotify))

    app.passthrough_tracker.update_keycode.assert_called_once_with(999)


def test_monitor_callback_routes_keypress_to_passthrough_tracker() -> None:
    """KeyPress / KeyRelease events must reach the bare-Super tap tracker —
    that's the whole point of opening a separate XRecord channel.
    """
    app = _make_app()
    event = MagicMock(type=X.KeyPress)
    app._monitor_callback(event)
    app.passthrough_tracker.handle_event.assert_called_once_with(event)


def test_schedule_rebind_cancels_previous_timer() -> None:
    """Two rebinds in quick succession must collapse to a single fire.

    A burst of triggers (suspend/resume + a udev hotplug at the same time)
    would otherwise queue redundant swaps, each of which broadcasts
    MappingNotify and contends for the mapper lock.
    """
    timer1 = MagicMock()
    timer2 = MagicMock()
    with patch("windowcharmer.main.threading.Timer", side_effect=[timer1, timer2]):
        app = _make_app()
        app._schedule_rebind()
        app._schedule_rebind()

    timer1.cancel.assert_called_once()
    timer1.start.assert_called_once()
    timer2.start.assert_called_once()
    timer2.cancel.assert_not_called()
    assert app.debounce_timer is timer2
