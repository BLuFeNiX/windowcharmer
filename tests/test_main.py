"""Unit tests for WindowCharmerApp orchestration logic.

The app glues together the window manager, keymap mapper, input manager,
input services, and super-tap tracker. Tests below exercise routing and
lifecycle with all collaborators mocked.
"""

from unittest.mock import patch

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
        patch("windowcharmer.main.RebindScheduler"),
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


def test_hotplug_and_sleep_route_through_rebind_scheduler() -> None:
    """Both hot-plug and sleep wake-up trigger the same debounced rebind path."""
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper"),
        patch("windowcharmer.main.InputManager") as input_manager_cls,
        patch("windowcharmer.main.InputServices") as input_services_cls,
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.RebindScheduler") as scheduler_cls,
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        app = WindowCharmerApp()

    schedule_fn = scheduler_cls.return_value.schedule
    assert input_manager_cls.call_args.kwargs["on_keyboard_hotplug"] is schedule_fn
    assert input_services_cls.call_args.kwargs["on_rebind_callback"] is schedule_fn
    assert app.rebind_scheduler is scheduler_cls.return_value


def test_run_daemon_drains_scheduler_before_mapper_cleanup() -> None:
    """Shutdown order matters: scheduler.shutdown must run before mapper.cleanup
    so a late-firing rebind can't re-swap the keymap after canonical restore.
    """
    order: list[str] = []
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper") as mapper_cls,
        patch("windowcharmer.main.InputManager"),
        patch("windowcharmer.main.InputServices") as services_cls,
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.RebindScheduler") as scheduler_cls,
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        services_cls.return_value.stop_all.side_effect = lambda: order.append("services")
        scheduler_cls.return_value.shutdown.side_effect = lambda: order.append("scheduler")
        mapper_cls.return_value.cleanup.side_effect = lambda: order.append("mapper")

        app = WindowCharmerApp()
        app.run_daemon()

    assert order == ["services", "scheduler", "mapper"]
