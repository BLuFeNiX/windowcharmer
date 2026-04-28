"""Unit tests for WindowCharmerApp orchestration logic.

The app glues together the window manager, keymap mapper, input manager,
sleep monitor, and super-tap tracker. Tests below exercise routing and
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
        patch("windowcharmer.main.WakeFromSleepDetector"),
        patch("windowcharmer.main.threading.Thread"),
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
        patch("windowcharmer.main.WakeFromSleepDetector") as sleep_detector_cls,
        patch("windowcharmer.main.threading.Thread") as thread_cls,
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.RebindScheduler") as scheduler_cls,
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        thread_cls.return_value.is_alive.return_value = False
        app = WindowCharmerApp()
        # Sleep monitor is constructed inside run_daemon, not __init__.
        app.run_daemon()

    schedule_fn = scheduler_cls.return_value.schedule
    assert input_manager_cls.call_args.kwargs["on_keyboard_hotplug"] is schedule_fn
    assert sleep_detector_cls.call_args.kwargs["callback"] is schedule_fn
    assert app.rebind_scheduler is scheduler_cls.return_value


def test_run_daemon_drains_scheduler_before_mapper_cleanup() -> None:
    """Shutdown order matters: sleep monitor must stop first (so no new
    debounce timers can be scheduled), then scheduler.shutdown drains any
    in-flight rebind, then mapper.cleanup restores the canonical keymap.

    Also asserts stop_event.set() runs BEFORE Thread.join(): otherwise join
    blocks the full 2s timeout in production because the detector never
    sees the stop signal.
    """
    order: list[str] = []
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper") as mapper_cls,
        patch("windowcharmer.main.InputManager"),
        patch("windowcharmer.main.WakeFromSleepDetector"),
        patch("windowcharmer.main.threading.Thread") as thread_cls,
        patch("windowcharmer.main.threading.Event") as event_cls,
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.RebindScheduler") as scheduler_cls,
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        event_cls.return_value.set.side_effect = lambda: order.append("stop_event")
        thread_cls.return_value.join.side_effect = lambda timeout: order.append("sleep_join")
        thread_cls.return_value.is_alive.return_value = False
        scheduler_cls.return_value.shutdown.side_effect = lambda: order.append("scheduler")
        mapper_cls.return_value.cleanup.side_effect = lambda: order.append("mapper")

        app = WindowCharmerApp()
        app.run_daemon()

    assert order == ["stop_event", "sleep_join", "scheduler", "mapper"]


def test_run_daemon_warns_when_sleep_thread_does_not_exit() -> None:
    """If join() returns and the thread is still alive, log a warning so the
    operator knows the daemon is leaving a hung thread behind on shutdown.
    """
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper"),
        patch("windowcharmer.main.InputManager"),
        patch("windowcharmer.main.WakeFromSleepDetector"),
        patch("windowcharmer.main.threading.Thread") as thread_cls,
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.RebindScheduler"),
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
        patch("windowcharmer.main.logger") as log,
    ):
        thread_cls.return_value.is_alive.return_value = True

        app = WindowCharmerApp()
        app.run_daemon()

    assert any(
        "did not exit" in (call.args[0] if call.args else "")
        for call in log.warning.call_args_list
    ), "expected a warning about the sleep monitor not exiting"


def test_run_daemon_can_be_called_twice() -> None:
    """The Thread is rebuilt each run_daemon(), so a second call doesn't
    raise RuntimeError on the already-started thread from the first call.
    """
    with (
        patch("windowcharmer.main.WindowManager"),
        patch("windowcharmer.main.KeyboardMapper"),
        patch("windowcharmer.main.InputManager"),
        patch("windowcharmer.main.WakeFromSleepDetector"),
        patch("windowcharmer.main.threading.Thread") as thread_cls,
        patch("windowcharmer.main.SuperPassthroughTracker"),
        patch("windowcharmer.main.RebindScheduler"),
        patch("windowcharmer.main.DisplayPool"),
        patch("windowcharmer.main.load_keybindings", return_value={}),
    ):
        thread_cls.return_value.is_alive.return_value = False

        app = WindowCharmerApp()
        app.run_daemon()
        app.run_daemon()

    # Two run_daemon() calls → two Thread() constructions.
    assert thread_cls.call_count == 2
