import logging
import subprocess
from collections.abc import Callable

logger = logging.getLogger(__name__)

ANIMATION_DURATION_MS = 180


_ANIMATE_BODY = """\
    let fr = mw.get_frame_rect();
    let shadowX = actor.x - fr.x, shadowY = actor.y - fr.y;
    let prevX = actor.x, prevY = actor.y;
    let newActorX = tx + shadowX, newActorY = ty + shadowY;

    let prevAnim = Main.animations_enabled;
    Main.animations_enabled = false;
    try {
        if (mw.maximized_horizontally || mw.maximized_vertically)
            mw.unmaximize(3);
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
        mode: 3
    });"""


def _make_script(xid: int, tx: int, ty: int, tw: int, th: int) -> str:
    return f"""\
(function() {{
    let tx = {tx}, ty = {ty}, tw = {tw}, th = {th};
    let dur = {ANIMATION_DURATION_MS};
    let actor = global.get_window_actors().find(a => a.meta_window.get_xwindow() === {xid});
    if (!actor) return 0;
    let mw = actor.meta_window;
    let Main = imports.ui.main;
{_ANIMATE_BODY}
    return 1;
}})()"""


def _make_batch_script(targets: list[tuple[int, int, int, int, int]]) -> str:
    entries = ", ".join(
        f"{{xid:{xid},tx:{tx},ty:{ty},tw:{tw},th:{th}}}"
        for xid, tx, ty, tw, th in targets
    )
    return f"""\
(function() {{
    let windows = [{entries}];
    let dur = {ANIMATION_DURATION_MS};
    let actors = global.get_window_actors();
    let Main = imports.ui.main;
    for (let w of windows) {{
        let actor = actors.find(a => a.meta_window.get_xwindow() === w.xid);
        if (!actor) continue;
        let mw = actor.meta_window;
        let tx = w.tx, ty = w.ty, tw = w.tw, th = w.th;
{_ANIMATE_BODY}
    }}
    return 1;
}})()"""


def _dbus_via_subprocess(script: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["dbus-send", "--session", "--print-reply",
         "--dest=org.Cinnamon", "/org/Cinnamon",
         "org.Cinnamon.Eval", f"string:{script}"],
        capture_output=True, text=True, timeout=3.0,
    )
    if result.returncode != 0:
        return False, result.stderr.strip()
    ok = False
    val = ""
    for line in result.stdout.splitlines():
        line = line.strip()
        if line == "boolean true":
            ok = True
        elif line.startswith('string "') and line.endswith('"'):
            val = line[8:-1]
    return ok, val


def _make_gi_caller() -> Callable[[str], tuple[bool, str]] | None:
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        def call(script: str) -> tuple[bool, str]:
            result = bus.call_sync(
                "org.Cinnamon", "/org/Cinnamon", "org.Cinnamon", "Eval",
                GLib.Variant("(s)", (script,)),
                GLib.VariantType("(bs)"),
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            ok, val = result.unpack()
            return bool(ok), str(val)

        return call
    except Exception:
        return None


class CinnamonAnimator:
    def __init__(self) -> None:
        self._available: bool | None = None
        gi_caller = _make_gi_caller()
        self._call: Callable[[str], tuple[bool, str]] = gi_caller or _dbus_via_subprocess

    def is_available(self) -> bool:
        if self._available is None:
            try:
                ok, val = self._call("1+1")
                self._available = ok and val == "2"
            except Exception:
                self._available = False
        return self._available

    def animate_batch(self, targets: list[tuple[int, int, int, int, int]]) -> bool:
        """Animate multiple windows simultaneously. targets: [(xid, tx, ty, tw, th), ...]"""
        if not self.is_available() or not targets:
            return False
        try:
            ok, val = self._call(_make_batch_script(targets))
            if not ok or val != "1":
                logger.debug("cinnamon animate_batch: ok=%s val=%r", ok, val)
                return False
            return True
        except Exception as e:
            logger.debug("cinnamon animate_batch error: %s", e)
            return False

    def animate(self, xid: int, tx: int, ty: int, tw: int, th: int) -> bool:
        """Slide window to (tx, ty, tw, th) via Cinnamon compositor animation."""
        if not self.is_available():
            return False
        try:
            ok, val = self._call(_make_script(xid, tx, ty, tw, th))
            if not ok or val != "1":
                logger.debug("cinnamon animate: ok=%s val=%r xid=%d", ok, val, xid)
                return False
            return True
        except Exception as e:
            logger.debug("cinnamon animate error: %s", e)
            return False
