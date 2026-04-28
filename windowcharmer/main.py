import argparse
import functools
import logging
import signal
import sys
import threading
from collections.abc import Callable

from Xlib import X
from Xlib.display import Display

from . import __version__
from .config.actions import TileAction
from .config.keybindings import load_keybindings
from .input.services import InputServices
from .input.super_passthrough import SuperPassthroughTracker
from .input.xi2_manager import InputManager, InputManagerError
from .tiling.manager import WindowManager
from .x11.display_pool import DisplayPool
from .x11.keyboard_mapper import KeyboardMapper

logger = logging.getLogger(__name__)

_REBIND_DEBOUNCE_SECONDS = 0.25


class WindowCharmerApp:
    def __init__(self, no_animate: bool = False) -> None:
        self.wm = WindowManager(no_animate=no_animate)
        self.key_bindings = load_keybindings()
        self.timer_lock = threading.Lock()
        self.debounce_timer: threading.Timer | None = None

        self.mapper = KeyboardMapper()
        self.input_dpy: Display = DisplayPool.get_display("input")

        # Tracker watches the *physical* Super key — stable across the swap.
        # Where the Super_L keysym lives moves when we swap; the physical key
        # doesn't, and that's what the user actually presses.
        self.passthrough_tracker = SuperPassthroughTracker(
            self.mapper.physical_super_kc(),
            self.mapper.simulate_hyper_press,
        )

        self.input_services = InputServices(on_rebind_callback=self._schedule_rebind)

        self.input_manager = InputManager(
            self.input_dpy,
            key_actions=self._setup_key_bindings(),
            passthrough_tracker=self.passthrough_tracker,
            on_keymap_change=self._on_keymap_change,
            on_keyboard_hotplug=self._schedule_rebind,
            modifier=X.Mod4Mask,
        )

    def stop(self) -> None:
        """Request a clean shutdown. Safe to call from a signal handler —
        InputManager.stop() sets a flag and pokes a wakeup pipe so a parked
        next_event() returns immediately.
        """
        self.input_manager.stop()

    def do_action(self, action: TileAction) -> None:
        if action == TileAction.EXIT:
            self.stop()
            return
        self.wm.execute_action(action)

    def _setup_key_bindings(self) -> dict[str, Callable[[], None]]:
        return {key: functools.partial(self.do_action, action) for key, action in self.key_bindings.items()}

    def _schedule_rebind(self) -> None:
        """Debounce a keymap rebind. Called from the sleep monitor thread and
        from the InputManager's HierarchyChanged handler (main thread)."""
        with self.timer_lock:
            if self.debounce_timer:
                self.debounce_timer.cancel()
            self.debounce_timer = threading.Timer(_REBIND_DEBOUNCE_SECONDS, self.mapper.apply_super_hyper_swap)
            self.debounce_timer.start()

    def _on_keymap_change(self) -> None:
        """Run on every MappingNotify(Keyboard) the InputManager sees.

        Two effects:
          1. Swap-apply: re-run apply_super_hyper_swap so the daemon's swap
             is preserved across external keymap edits (setxkbmap, etc.).
             apply_super_hyper_swap is idempotent against its own broadcast,
             so we can call it directly here without debouncing.
          2. Tracker re-key: physical_super_kc() may have moved if setxkbmap
             relocated Super_L; tell the passthrough tracker its new home.
        """
        logger.debug("MappingNotify for Keyboard, applying swap and refreshing tracker keycode")
        self.mapper.apply_super_hyper_swap()
        self.passthrough_tracker.update_keycode(self.mapper.physical_super_kc())

    def run_daemon(self) -> None:
        logger.info("Starting WindowCharmer Daemon...")
        self.mapper.apply_super_hyper_swap()
        self.input_services.start_all()

        try:
            logger.info("Daemon started. Press Ctrl+C to exit.")
            self.input_manager.start()
        except KeyboardInterrupt:
            pass
        finally:
            # Stop background services first so no new debounce timers can be
            # scheduled, then cancel + join the in-flight one. Joining
            # guarantees a pending rebind completes before mapper.cleanup()
            # restores the canonical mapping — otherwise a late-firing rebind
            # would re-swap after cleanup.
            self.input_services.stop_all()
            with self.timer_lock:
                timer = self.debounce_timer
                self.debounce_timer = None
            if timer:
                timer.cancel()
                timer.join()
                logger.debug("Joined debounce timer at shutdown")
            self.mapper.cleanup()
            DisplayPool.close_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="WindowCharmer Daemon")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("--no-animate", action="store_true", help="Disable Cinnamon compositor animations")
    parser.add_argument("--fix-keymap", action="store_true", help="Restore canonical Super_L/Hyper_L mapping and exit")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    if args.fix_keymap:
        try:
            mapper = KeyboardMapper()
            if not mapper.force_canonical():
                print("Super_L or Hyper_L not found in keyboard mapping.", file=sys.stderr)
                sys.exit(1)
            print("Keyboard mapping fixed.")
        finally:
            DisplayPool.close_all()
        sys.exit(0)

    app = WindowCharmerApp(no_animate=args.no_animate)
    # Install AFTER construction so the handler can reference app. stop() is
    # signal-safe (sets a flag and writes one byte non-blocking), which wakes
    # the loop immediately rather than waiting for the next X event.
    # SIGHUP is handled too so a session-end or `kill -HUP` runs cleanup —
    # otherwise the keymap stays inverted until next X login.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: app.stop())
    try:
        app.run_daemon()
    except InputManagerError as e:
        logger.error("Daemon stopped: %s", e)
        logger.debug("", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
