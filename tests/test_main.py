"""Unit tests for WindowCharmerApp orchestration logic.

The app glues together the window manager, keymap mapper, input manager,
input services, and super-tap tracker. Tests below exercise routing and
lifecycle with all collaborators mocked.
"""

from unittest.mock import MagicMock, patch

from windowcharmer.config.actions import TileAction
from windowcharmer.main import WindowCharmerApp


def _make_app() -> WindowCharmerApp:
    """Construct an app with every external collaborator mocked."""
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper"),
        patch("windowcharmer.main.InputManager"),
        patch("windowcharmer.main.InputServices"),
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        return WindowCharmerApp()


def test_do_action_routes_tile_to_window_manager() -> None:
    app = _make_app()
    app.do_action(TileAction.LEFT)
    app.wm.execute_action.assert_called_once_with(TileAction.LEFT)
    app.input_manager.stop.assert_not_called()


def test_do_action_exit_short_circuits_to_stop() -> None:
    """EXIT must shut down the input manager loop and never reach the wm."""
    app = _make_app()
    app.do_action(TileAction.EXIT)
    app.input_manager.stop.assert_called_once()
    app.wm.execute_action.assert_not_called()


def test_on_keymap_change_applies_swap_and_refreshes_tracker_keycode() -> None:
    """MappingNotify(Keyboard) reaches us via the InputManager. Two effects
    must fire under the same handler: re-apply the swap (keymap may have been
    edited by setxkbmap), and refresh the tracker's keycode (physical Super
    may have moved).
    """
    app = _make_app()
    app.mapper.physical_super_kc.return_value = 999

    app._on_keymap_change()

    app.mapper.apply_super_hyper_swap.assert_called_once()
    app.passthrough_tracker.update_keycode.assert_called_once_with(999)


def test_schedule_rebind_cancels_previous_timer() -> None:
    """Two rebinds in quick succession must collapse to a single fire."""
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
