import contextlib
import logging
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import NamedTuple

from Xlib import X
from Xlib.error import BadDrawable, BadWindow, ConnectionClosedError, DisplayConnectionError
from Xlib.xobject.drawable import Window

from ..cinnamon.animator import CinnamonAnimator
from ..config.actions import TileAction
from ..config.dimensions import ScreenDimensions
from ..config.settings import Config
from ..x11.display_pool import DisplayPool
from ..x11.ewmh_client import ALL_DESKTOPS, EwmhClient
from .zones import classify_zone, determine_tile_zone

logger = logging.getLogger(__name__)


class _ZoneSpec(NamedTuple):
    # geom=None marks a flag-only action (MAX, RESTORE) that touches
    # _NET_WM_STATE without issuing a configure. For tile actions
    # (geom set) v_max/h_max are unused — we deliberately do NOT direct-write
    # _NET_WM_STATE for tiles because Muffin resyncs in response and CSD
    # apps (e.g. Brave) flicker; the geometry write alone leaves the
    # window correctly placed without disturbing the WM's focus stack.
    geom: Callable[[ScreenDimensions], tuple[int, int, int, int]] | None
    v_max: int = 0
    h_max: int = 0
    needs_center: bool = False


_REMAP_NO_CENTER: dict[str, str] = {
    "left-center": "left",
    "right-center": "right",
    "center": "left",
    "top-center": "top-left",
    "bottom-center": "bottom-left",
}


def _remap_zone_no_center(zone: str) -> str:
    """Map center-spanning zones to their side-only equivalents when center_width == 0.

    Center-anchored zones collapse to the left column (left vs right is
    arbitrary — they had to go somewhere); non-center zones pass through.
    """
    return _REMAP_NO_CENTER.get(zone, zone)


def _resolve_zone_actions(
    window_zones: Iterable[tuple[Window, str]],
    has_center: bool,
) -> Iterator[tuple[Window, TileAction]]:
    """Yield (win, TileAction) pairs, applying the no-center remap when has_center is False.

    Skips zones that don't correspond to a TileAction (e.g. 'top-left-center'
    — a half-height window spanning side+center, reachable via manual drag).
    """
    for win, zone in window_zones:
        if not has_center:
            zone = _remap_zone_no_center(zone)
        try:
            yield win, TileAction(zone)
        except ValueError:
            logger.debug("Skipping window in non-tileable zone %r", zone)
            continue


# fmt: off
_TILE_SPEC: dict[TileAction, _ZoneSpec] = {
    TileAction.LEFT:          _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side,               d.h_full)),
    TileAction.LEFT_CENTER:   _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side + d.w_center,  d.h_full), needs_center=True),  # noqa: E501
    TileAction.RIGHT:         _ZoneSpec(lambda d: (d.x_right,  d.y_top,    d.w_side,               d.h_full)),
    TileAction.RIGHT_CENTER:  _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center + d.w_side,  d.h_full), needs_center=True),  # noqa: E501
    TileAction.CENTER:        _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center, d.h_full), needs_center=True),
    TileAction.TOP_LEFT:      _ZoneSpec(lambda d: (d.x_left,   d.y_top,    d.w_side,   d.h_half)),
    TileAction.BOTTOM_LEFT:   _ZoneSpec(lambda d: (d.x_left,   d.y_bottom, d.w_side,   d.h_half)),
    TileAction.TOP_RIGHT:     _ZoneSpec(lambda d: (d.x_right,  d.y_top,    d.w_side,   d.h_half)),
    TileAction.BOTTOM_RIGHT:  _ZoneSpec(lambda d: (d.x_right,  d.y_bottom, d.w_side,   d.h_half)),
    TileAction.TOP_CENTER:    _ZoneSpec(lambda d: (d.x_center, d.y_top,    d.w_center, d.h_half), needs_center=True),
    TileAction.BOTTOM_CENTER: _ZoneSpec(lambda d: (d.x_center, d.y_bottom, d.w_center, d.h_half), needs_center=True),
    TileAction.MAX:           _ZoneSpec(None, 1, 1),
}
# fmt: on
# RESTORE has no _TILE_SPEC entry — it's a special case in _apply_tile_action
# that returns a window to its captured spawn geometry.


class WindowManager:
    def __init__(self, no_animate: bool = False) -> None:
        """Initializes the WindowManager with its own X11 display connection."""
        self.d = DisplayPool.get_display("wm")
        self.props = EwmhClient(self.d)

        # Config starts unsized; _update_state populates wa_w from _NET_WORKAREA
        # on the first action.
        self.config: Config = Config(wa_w=0)
        self.dim: ScreenDimensions | None = None
        self.animator: CinnamonAnimator = CinnamonAnimator(disabled=no_animate)

        # win.id → (x, y, w, h) at first sight. RESTORE returns the window
        # there. Refreshed on every action by _track_windows: new ids get
        # snapshotted, vanished ids get pruned.
        self._spawn_geom: dict[int, tuple[int, int, int, int]] = {}

        # Super+Tab cycle session state. ``_cycle_session`` is the bucket
        # snapshotted at the first chord press while Super is held — a
        # list ordered top-to-bottom of stacking. ``_cycle_cursor`` is
        # the position currently on top after the most recent cycle
        # press. Held repeats advance the cursor through the *snapshot*
        # (not the live stack, which reorders after every activate);
        # this is what makes "hold Super, tap Tab repeatedly" walk
        # progressively deeper rather than ping-pong between two
        # windows. Both cleared by ``end_cycle_session`` on Super
        # release or any non-CYCLE action.
        self._cycle_session: list[Window] | None = None
        self._cycle_cursor: int = 0

    def _update_state(self) -> None:
        """Refresh screen layout from X server. Uses _NET_WORKAREA for panel-aware geometry."""
        active_desktop = self.props.get_active_desktop()

        workarea = self.props.get_workarea()
        if workarea:
            wa_x, wa_y, wa_w, wa_h = workarea
        else:
            wa_x, wa_y = 0, 0
            wa_w, wa_h = self.props.get_screen_size()

        # Workarea must reach Config before reload so center_width is sized
        # against usable area, not raw screen width.
        self.config.set_state(wa_w, active_desktop)

        self.dim = ScreenDimensions(
            wa_x,
            wa_y,
            wa_w,
            wa_h,
            self.config.center_width,
        )

    def _track_windows(self) -> None:
        """Refresh _spawn_geom: snapshot new windows, drop vanished ones,
        and refresh existing snapshots when the window is observed in a
        non-tile (natural) state.

        Called at the top of every action. The refresh-on-natural rule lets
        manual drag-resize update the snapshot — a window the user has moved
        to a non-tile geometry is definitionally in their preferred state, so
        RESTORE should return there. Tiled windows are preserved: their
        current geometry is our doing, not the user's.
        """
        live = self.props.list_windows()
        live_ids = {win.id for win in live}
        before = set(self._spawn_geom)
        self._spawn_geom = {wid: g for wid, g in self._spawn_geom.items() if wid in live_ids}
        pruned = before - set(self._spawn_geom)
        if pruned:
            logger.debug("Pruned %d dead window snapshot(s): %s", len(pruned), [hex(p) for p in pruned])

        for win in live:
            try:
                geom = win.get_geometry()
                coords = win.translate_coords(geom.root, 0, 0)
            except (BadWindow, BadDrawable):
                continue
            if not coords:
                continue
            cur = (abs(coords.x), abs(coords.y), geom.width, geom.height)

            existing = self._spawn_geom.get(win.id)
            if existing is None:
                self._spawn_geom[win.id] = cur
                logger.debug("Snapshotted 0x%x at (x=%d, y=%d, w=%d, h=%d)", win.id, *cur)
                continue

            if cur == existing:
                continue

            # Geometry differs from the snapshot. Refresh only when the
            # window is in a non-tile state — that means the user (not us)
            # put it there, so it's the new "preferred" geometry.
            if not self.dim:
                continue
            try:
                is_max_v = self.props.is_window_maximized_vertically(win)
            except (BadWindow, BadDrawable):
                continue
            zone = classify_zone(*cur, self.dim, is_max_v)
            if "unknown" in zone:
                self._spawn_geom[win.id] = cur
                logger.debug("Refreshed 0x%x snapshot to (x=%d, y=%d, w=%d, h=%d)", win.id, *cur)

    def _raise_window(self, window: Window, timestamp: int = X.CurrentTime) -> None:
        """Raise a window to the top of the stack.

        Tries Mutter's mw.activate via the Cinnamon animator first when
        available — that runs inside the WM process and bypasses focus-
        stealing prevention entirely. Falls back to the X11 EWMH path
        for non-Cinnamon WMs (and the no-extra install on Cinnamon).

        The X11 fallback passes ``timestamp`` (the chord's X server
        time) into the activate ClientMessage. Without a recent
        timestamp, Mutter / Muffin / KWin compare against the target's
        ``_NET_WM_USER_TIME``, decide our request is older / zero, and
        silently drop it as focus-stealing — leaving freshly-tiled
        windows buried under existing same-zone tiles.

        The window is already focused at this call site (it's the
        active window we just tiled), so activate is a no-op for focus
        and only reorders the stack.
        """
        if self.animator.activate(window.id):
            return
        with contextlib.suppress(BadWindow, BadDrawable):
            self.props.activate_window(window, timestamp)

    def _restore_window(self, window: Window) -> None:
        """Return a window to its captured spawn geometry, or no-op if untracked."""
        snap = self._spawn_geom.get(window.id)
        logger.debug(
            "RESTORE: window=0x%x, snap=%s, total_tracked=%d",
            window.id,
            snap,
            len(self._spawn_geom),
        )
        if snap is None:
            return

        # WMs ignore configure() while max/fullscreen are set — clear those
        # first so the geometry actually lands. The pre-checks avoid an
        # unnecessary _NET_WM_STATE ClientMessage when the flag is already
        # absent (Muffin reacts to writes; see project memory).
        if self.props.is_window_maximized_vertically(window) or self.props.is_window_maximized_horizontally(window):
            self.props.set_max_flags(window, 0, 0)
        if self.props.is_window_fullscreen(window):
            self.props.set_fullscreen_flag(window, on=False)

        x, y, w, h = snap
        # Skip move_and_resize's frame-extents math: the snapshot IS the X11
        # client rect (that's what get_geometry returned), so adjusting again
        # would over-correct by 2x the extents.
        logger.debug("RESTORE: configuring 0x%x to (x=%d, y=%d, w=%d, h=%d)", window.id, x, y, w, h)
        window.configure(value_mask=X.CWX | X.CWY | X.CWWidth | X.CWHeight, x=x, y=y, width=w, height=h)

    def _resolve_tile_cycle(self, action: TileAction, win: Window) -> TileAction:
        """Cycle LEFT↔LEFT_CENTER and RIGHT↔RIGHT_CENTER based on the window's current zone."""
        if self.config.center_width == 0:
            return action
        zone = determine_tile_zone(win, self.dim, self.props.is_window_maximized_vertically(win))
        if action == TileAction.LEFT:
            if zone == "left":
                return TileAction.LEFT_CENTER
            if zone == "left-center":
                return TileAction.LEFT
        elif action == TileAction.RIGHT:
            if zone == "right":
                return TileAction.RIGHT_CENTER
            if zone == "right-center":
                return TileAction.RIGHT
        return action

    @contextmanager
    def _grabbed(self) -> Iterator[None]:
        """Hold an X server grab for the duration of the block."""
        self.d.grab_server()
        try:
            yield
        finally:
            self.d.ungrab_server()
            self.d.flush()

    def execute_action(self, action: TileAction, timestamp: int = X.CurrentTime) -> None:
        """Entry point for a tiling action, called from the InputManager event loop on the main thread.

        ``timestamp`` is the X server time of the chord that triggered
        the action (XI2 ``data.time``). It is forwarded to every EWMH
        activate so Mutter's focus-stealing prevention treats the
        request as a recent user gesture rather than dropping it. The
        ``X.CurrentTime`` default exists for callers that have no
        timestamp (synthetic / programmatic invocations); chord-driven
        invocations always pass the real time.
        """
        logger.debug("execute_action: %s (time=%d)", action, timestamp)
        # Any non-CYCLE action ends an in-flight cycle session: the user
        # has clearly moved on (tile, max, restore, …) and a subsequent
        # CYCLE press should start fresh.
        if action != TileAction.CYCLE:
            self.end_cycle_session()
        try:
            # Read state before grabbing the server to minimise the held window.
            self._update_state()
            # Snapshot any newly-seen windows so RESTORE can return them later.
            # Runs before any tile mutation so the captured geom is pre-tile.
            self._track_windows()

            if action in (TileAction.BIGGER, TileAction.SMALLER):
                step = 1 if action == TileAction.BIGGER else -1
                # Animated path runs outside grab_server because Cinnamon is a
                # separate X11 client and would deadlock against our grab.
                if self._try_animated_resize_all(step):
                    return
                with self._grabbed():
                    self.resize_all_windows(step)
                return

            if action == TileAction.CYCLE:
                self._cycle_below(timestamp)
                return

            win = self.props.get_active_window()
            if win is None:
                return
            if action in (TileAction.LEFT, TileAction.RIGHT):
                action = self._resolve_tile_cycle(action, win)
            if action in _TILE_SPEC and self._try_animated_tile(action, win):
                # The animated path activates inside the same JS call
                # that does the move_resize_frame — atomic from Mutter's
                # POV. No follow-up raise needed.
                return
            with self._grabbed():
                self._apply_tile_action(action, win)
            # Non-animated path: explicit raise so the freshly-tiled
            # window comes forward over any existing same-zone tile.
            # Skipped for BIGGER/SMALLER (they preserve stacking) and
            # CYCLE (it raises a different window inside _cycle_below).
            self._raise_window(win, timestamp)
        except (ConnectionClosedError, DisplayConnectionError):
            raise  # unrecoverable; propagate so the supervisor can restart
        except Exception as e:
            logger.error("Error executing action %s: %s", action, e)
            logger.debug("", exc_info=True)

    def _try_animated_tile(self, action: TileAction, win: Window) -> bool:
        """Attempt to animate a tile via Cinnamon compositor. Returns True if handled."""
        spec = _TILE_SPEC.get(action)
        if not self.dim or spec is None or spec.geom is None:
            return False
        if spec.needs_center and self.config.center_width == 0:
            return False
        x, y, w, h = spec.geom(self.dim)
        return self.animator.animate(win.id, x, y, w, h)

    def _try_animated_resize_all(self, step: int) -> bool:
        """Animate all tiled windows to the next ratio in one compositor call."""
        if not self.dim:
            return False

        window_zones = self._collect_zoned_windows()
        if not window_zones:
            return False

        # Compute what the layout will look like after the ratio step, without
        # committing the change yet so the fallback path can do it if needed.
        next_center_width = self.config.peek_center_width(step)
        next_dim = ScreenDimensions(self.dim.wa_x, self.dim.wa_y, self.dim.wa_w, self.dim.wa_h, next_center_width)

        targets = [
            (win.id, x, y, w, h)
            for win, x, y, w, h in self._resolve_tile_targets(window_zones, next_dim, has_center=next_center_width > 0)
        ]
        if not targets:
            return False

        if not self.animator.animate_batch(targets):
            return False

        # Commit ratio change only after successful animation.
        self.config.next_ratio(step)
        self._update_state()
        return True

    def _resolve_tile_targets(
        self, window_zones: list[tuple[Window, str]], dim: ScreenDimensions, has_center: bool
    ) -> Iterator[tuple[Window, int, int, int, int]]:
        """Map (window, zone) pairs to (window, x, y, w, h) targets for the given layout.

        Skips windows whose resolved action has no geometry (MAX/RESTORE
        can't appear from zone strings, but the type allows it) and those
        that need the center column when it's absent.
        """
        for win, action in _resolve_zone_actions(window_zones, has_center=has_center):
            spec = _TILE_SPEC.get(action)
            if not spec or spec.geom is None or (spec.needs_center and not has_center):
                continue
            x, y, w, h = spec.geom(dim)
            yield win, x, y, w, h

    def _apply_tile_action(self, action: TileAction, win: Window) -> None:
        """Dispatch a tile action using the _TILE_SPEC table."""
        if action == TileAction.RESTORE:
            self._restore_window(win)
            return

        spec = _TILE_SPEC.get(action)
        if spec is None:
            logger.warning("Unhandled tile action: %s", action)
            return

        if spec.geom is None:
            # Flag-only action (MAX).
            self.props.set_max_flags(win, spec.v_max, spec.h_max)
            return

        if not self.dim:
            return
        if spec.needs_center and self.config.center_width == 0:
            logger.warning("Action %s requires the center column; ignoring (center width is 0)", action)
            return

        x, y, w, h = spec.geom(self.dim)
        self.move_and_resize(win, x, y, w, h)

    def move_and_resize(self, window: Window, x: int, y: int, width: int, height: int) -> None:
        """Fits a window into (x, y, width, height), accounting for GTK CSD and WM frames."""
        x, y, client_w, client_h = self._compute_client_geometry(window, x, y, width, height)

        # Maximized and fullscreen windows ignore configure() — the WM owns
        # their geometry. Clear those flags first or the configure will be
        # rejected (or briefly applied then snapped back, producing a flicker).
        if self.props.is_window_maximized_vertically(window) or self.props.is_window_maximized_horizontally(window):
            self.props.set_max_flags(window, 0, 0)
        if self.props.is_window_fullscreen(window):
            self.props.set_fullscreen_flag(window, on=False)

        window.configure(
            value_mask=X.CWX | X.CWY | X.CWWidth | X.CWHeight,
            x=x,
            y=y,
            width=client_w,
            height=client_h,
        )

    def _compute_client_geometry(
        self, window: Window, x: int, y: int, width: int, height: int
    ) -> tuple[int, int, int, int]:
        """Translate a target visible rect into the X11 client geometry.

        GTK CSD windows include shadows inside the X11 window — shift the origin
        out and grow the size to absorb them. WM-decorated windows have a frame
        outside the X11 client — shrink the size to leave room for the frame.
        """
        gtk_fe = self.props.get_gtk_frame_extents(window)
        if gtk_fe:
            x -= gtk_fe.left
            y -= gtk_fe.top
            width += gtk_fe.left + gtk_fe.right
            height += gtk_fe.top + gtk_fe.bottom

        net_fe = self.props.get_net_frame_extents(window)
        if net_fe:
            width -= net_fe.left + net_fe.right
            height -= net_fe.top + net_fe.bottom

        if width < 1 or height < 1:
            logger.debug("Frame-extents math underflow: requested %dx%d → clamped to 1x1", width, height)
        return x, y, max(1, width), max(1, height)

    def resize_all_windows(self, step: int) -> None:
        """Adjusts the center-column ratio for all tiled windows on the active desktop."""
        # Snapshot zones BEFORE committing the new ratio: zone detection
        # matches windows against their current positions, but tile geometry
        # is computed against the post-commit layout.
        window_zones = self._collect_zoned_windows()

        self.config.next_ratio(step)
        self._update_state()
        if not self.dim:
            return

        for win, x, y, w, h in self._resolve_tile_targets(
            window_zones, self.dim, has_center=self.config.center_width > 0
        ):
            self.move_and_resize(win, x, y, w, h)

    def end_cycle_session(self) -> None:
        """Clear the in-flight Super+Tab cycle session.

        Called from the SuperPassthroughTracker on Super release — that
        ends a "held" cycle and makes the next chord press start a
        fresh session. Also called at the top of ``execute_action``
        for any non-CYCLE action: pressing Super+Right or Super+Up
        while held mid-cycle is the user moving on, not continuing.
        """
        self._cycle_session = None
        self._cycle_cursor = 0

    def _cycle_below(self, timestamp: int = X.CurrentTime) -> None:
        """Alt-Tab semantics for the focused window's bucket.

        First chord press (no in-flight session) activates the
        second-from-top window in the bucket — like releasing Alt
        immediately after a single Alt+Tab. Continued presses while
        Super is still held advance one position deeper into the
        bucket on each press, walking the snapshot taken at session
        start. Releasing Super clears the session so the next press
        starts fresh.

        Bucket membership requires:

          * Same zone as the active window (tile zone exact match, or
            both classified as "unknown" for floating windows). The
            ``_ZONE_DEVIATION`` (128 px) tolerance is intentionally
            loose so that windows tiled to the same zone with very
            different frame extents — e.g. a calculator app whose
            decoration is 84 px taller than a terminal's — still
            count as "the same tile" from the user's POV.
          * NORMAL window type and currently mapped/viewable. These
            two filters together exclude popups, dialogs, dock
            windows, tooltips, and minimized / iconified windows
            that are still in ``_NET_CLIENT_LIST_STACKING`` but
            invisible to the user.
          * Same desktop (or sticky).

        Why the held cycle walks a snapshot and not the live stack —
        every press activates immediately, and Mutter reorders the
        stack on each activate. Recomputing "second-from-top" from
        the live stack on every held press would just ping-pong
        between the two front-most windows; freezing the bucket at
        session start so the cursor advances through it gives a
        cursor that reaches genuinely deeper windows on each tap.

        Cursor wrap: ``cursor`` advances modulo ``len(session)``.
        After the deepest member, wrap-to-0 reactivates the original-
        top — it isn't currently on top of the stack any more (the
        held cycle has been promoting deeper windows past it), so
        bringing it forward is a real visual change and the bucket's
        full N-cycle stays intact.
        """
        if self._cycle_session is None:
            active = self.props.get_active_window()
            if active is None:
                logger.debug("CYCLE: no active window — skipping")
                return
            try:
                active_zone = determine_tile_zone(active, self.dim, self.props.is_window_maximized_vertically(active))
            except (BadWindow, BadDrawable) as e:
                logger.debug("CYCLE: active window vanished while reading zone: %s", e)
                return

            stack = self.props.list_windows()
            bucket = [w for w in reversed(stack) if self._is_cycle_target(w, active_zone)]
            if len(bucket) <= 1:
                logger.debug("CYCLE: bucket has %d window(s) — nothing to cycle", len(bucket))
                return
            self._cycle_session = bucket
            self._cycle_cursor = 1
            logger.debug(
                "CYCLE: new session of %d window(s), cursor=1, zone=%r",
                len(bucket),
                active_zone,
            )
        else:
            self._cycle_cursor = (self._cycle_cursor + 1) % len(self._cycle_session)
            logger.debug(
                "CYCLE: continued session, cursor=%d/%d",
                self._cycle_cursor,
                len(self._cycle_session) - 1,
            )

        target = self._cycle_session[self._cycle_cursor]
        logger.debug("CYCLE: activating 0x%x", target.id)
        # Prefer Mutter's mw.activate (via the Cinnamon animator) over
        # the X11 EWMH path: it runs inside the WM process so focus-
        # stealing prevention does not apply. The X11 fallback passes
        # the chord's X server timestamp so Mutter / Muffin / KWin
        # treat the request as a recent user gesture rather than
        # dropping it as focus-stealing. Suppress BadWindow/BadDrawable
        # in case the session contains a window that's been closed
        # mid-cycle — better than exploding execute_action's outer
        # error handler with a stack trace.
        if not self.animator.activate(target.id):
            with contextlib.suppress(BadWindow, BadDrawable):
                self.props.activate_window(target, timestamp)

    def _is_cycle_target(self, window: Window, target_zone: str) -> bool:
        """True when `window` belongs in the active window's cycle bucket.

        Filters applied (in order, cheapest first):

          1. ``_NET_WM_WINDOW_TYPE_NORMAL`` (or unset) — exclude
             dialogs, dock, menu, notification, tooltip, splash.
          2. ``map_state == IsViewable`` — exclude iconified /
             withdrawn windows that are still in the stacking list
             but invisible to the user.
          3. Same desktop or sticky.
          4. Same zone bucket: tile zones match by exact string
             (``"left"`` matches ``"left"`` but not ``"left-center"``);
             any zone containing ``"unknown"`` shares one floating
             bucket with all other unknown shapes. The 128 px
             ``_ZONE_DEVIATION`` is intentionally loose so that
             cross-app frame-extents differences (a calculator
             whose decoration is 84 px taller than a terminal's,
             for example) still count as "the same tile" — these
             are real windows the user has placed in the same zone,
             just rendered at slightly different shapes.

        Sticky windows (_NET_WM_DESKTOP == ALL_DESKTOPS) qualify on
        every desktop. Windows that vanish mid-check are excluded.
        """
        try:
            if not self.props.is_normal_window(window):
                return False
            if not self.props.is_viewable_window(window):
                return False
            desktop = self.props.get_window_desktop(window)
            if desktop != self.config.active_desktop and desktop != ALL_DESKTOPS:
                return False
            zone = determine_tile_zone(window, self.dim, self.props.is_window_maximized_vertically(window))
        except (BadWindow, BadDrawable):
            return False
        if "unknown" in target_zone:
            return "unknown" in zone
        return zone == target_zone

    def _collect_zoned_windows(self) -> list[tuple[Window, str]]:
        """Return (window, zone) pairs for all tiled windows on the active desktop.

        Windows on other desktops, in unknown zones, or that vanish mid-scan
        are skipped. Zones may be strings that don't map to a TileAction
        (e.g. 'top-left-center'); callers must guard against that.
        """
        result: list[tuple[Window, str]] = []
        for win in self.props.list_windows():
            try:
                desktop = self.props.get_window_desktop(win)
                if desktop != self.config.active_desktop and desktop != ALL_DESKTOPS:
                    continue
                zone = determine_tile_zone(win, self.dim, self.props.is_window_maximized_vertically(win))
                if "unknown" not in zone:
                    result.append((win, zone))
            except (BadWindow, BadDrawable) as e:
                logger.debug("Skipping window during tiled-zone scan: %s", e)
        return result
