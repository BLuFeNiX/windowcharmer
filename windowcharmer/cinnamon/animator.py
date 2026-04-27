import logging
import os
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

ANIMATION_DURATION_MS = 180

# D-Bus call deadline. Healthy Cinnamon round-trips are sub-millisecond, so
# 50 ms is ~25x headroom while still well under the ~100 ms "instantaneous"
# threshold. Exceeding it means Cinnamon is wedged; we fall back to a
# non-animated snap and let the cooldown logic decide when to retry.
ANIMATION_DEADLINE_MS = 50

# How long to wait before re-probing after a failed availability check.
_PROBE_COOLDOWN_S = 30.0


_ANIMATE_FN = """\
function _wcAnimate(actor, tx, ty, tw, th, dur) {
    let mw = actor.meta_window;
    let Main = imports.ui.main;
    let fr = mw.get_frame_rect();
    let shadowX = actor.x - fr.x, shadowY = actor.y - fr.y;
    let prevX = actor.x, prevY = actor.y;
    let newActorX = tx + shadowX, newActorY = ty + shadowY;

    let prevAnim = Main.animations_enabled;
    Main.animations_enabled = false;
    try {
        if (mw.maximized_horizontally || mw.maximized_vertically)
            mw.unmaximize(3);  // Meta.MaximizeFlags.BOTH
    } finally {
        Main.animations_enabled = prevAnim;
    }

    mw.move_resize_frame(false, tx, ty, tw, th);
    actor.remove_all_transitions();

    actor.translation_x = prevX - newActorX;
    actor.translation_y = prevY - newActorY;
    actor.ease({
        translation_x: 0,
        translation_y: 0,
        duration: dur,
        mode: 3  // Clutter.AnimationMode.EASE_IN_OUT_QUAD
    });
}"""


def _make_batch_script(targets: list[tuple[int, int, int, int, int]]) -> str:
    """Build the JS payload for animating a batch of windows.

    Returns the count of actors actually animated, so a single-window animate
    can distinguish 'actor not found' (0) from success (1) and fall back to
    the non-animated configure() path.
    """
    entries = ", ".join(f"{{xid:{xid},tx:{tx},ty:{ty},tw:{tw},th:{th}}}" for xid, tx, ty, tw, th in targets)
    return f"""\
(function() {{
{_ANIMATE_FN}
    let windows = [{entries}];
    let actors = global.get_window_actors();
    let count = 0;
    for (let w of windows) {{
        let actor = actors.find(a => a.meta_window.get_xwindow() === w.xid);
        if (!actor) continue;
        _wcAnimate(actor, w.tx, w.ty, w.tw, w.th, {ANIMATION_DURATION_MS});
        count++;
    }}
    return count;
}})()"""


def _make_gi_caller() -> Callable[[str], tuple[bool, str]] | None:
    try:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def call(script: str) -> tuple[bool, str]:
            result = bus.call_sync(
                "org.Cinnamon",
                "/org/Cinnamon",
                "org.Cinnamon",
                "Eval",
                GLib.Variant("(s)", (script,)),
                GLib.VariantType("(bs)"),
                Gio.DBusCallFlags.NONE,
                ANIMATION_DEADLINE_MS,
                None,
            )
            ok, val = result.unpack()
            return bool(ok), str(val)

        return call
    except Exception as e:
        logger.debug("gi/D-Bus setup failed: %s", e)
        return None


def _on_cinnamon() -> bool:
    return "cinnamon" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


class CinnamonAnimator:
    def __init__(self, disabled: bool = False) -> None:
        self._disabled = disabled
        self._available: bool | None = None
        self._last_probe: float = 0.0
        self._call: Callable[[str], tuple[bool, str]] | None = None
        if disabled:
            return
        self._call = _make_gi_caller()
        if self._call is None:
            self._disabled = True
            if _on_cinnamon():
                logger.warning(
                    "Cinnamon detected but tile animations are disabled: PyGObject is unavailable. "
                    "Reinstall with the [cinnamon] extra to enable animations (see README)."
                )

    def is_available(self) -> bool:
        if self._disabled or self._call is None:
            return False
        if self._available is True:
            return True
        now = time.monotonic()
        if self._available is None or (now - self._last_probe) >= _PROBE_COOLDOWN_S:
            self._last_probe = now
            try:
                ok, val = self._call("1+1")
                self._available = bool(ok and val == "2")
            except Exception:
                self._available = False
        return self._available is True

    def _invoke(self, script: str) -> str | None:
        """Run a script via D-Bus. On transport failure, reset cached
        availability so the next is_available() call re-probes — otherwise
        every animation attempt would block on the dead bus until restart.
        Returns the eval result string, or None on transport failure.
        """
        # is_available() guarantees self._call is not None at every call site,
        # but bind to a local so the type narrowing survives without an assert.
        call = self._call
        if call is None:
            return None
        try:
            ok, val = call(script)
        except Exception as e:
            logger.debug("cinnamon eval error: %s", e)
            self._available = None
            return None
        if not ok:
            logger.debug("cinnamon eval rejected: val=%r", val)
            self._available = None
            return None
        return val

    def animate_batch(self, targets: list[tuple[int, int, int, int, int]]) -> bool:
        """Animate multiple windows simultaneously. targets: [(xid, tx, ty, tw, th), ...]

        Returns True if at least one actor was animated. Per-window misses
        (actor not found) are not a Cinnamon-down signal, so cached
        availability is left intact.
        """
        if not self.is_available() or not targets:
            return False
        val = self._invoke(_make_batch_script(targets))
        if val is None:
            return False
        if val == "0":
            logger.debug("cinnamon animate_batch: no actors found for %d target(s)", len(targets))
            return False
        return True

    def animate(self, xid: int, tx: int, ty: int, tw: int, th: int) -> bool:
        """Slide window to (tx, ty, tw, th) via Cinnamon compositor animation."""
        return self.animate_batch([(xid, tx, ty, tw, th)])
