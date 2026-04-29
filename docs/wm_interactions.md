# WM Interaction Pitfalls

Notes for anyone adding a feature that needs to raise, focus, or otherwise
"activate" a window. The EWMH spec makes these operations look like
single-call affairs. In practice they fail silently in WM-specific ways
and the symptoms are user-visible (Super+Tab does nothing, freshly-tiled
windows land buried, focus jumps to the wrong app). This doc catalogues
the pitfalls so the next person doesn't have to rediscover them.

---

## Activation is not one operation

EWMH gives you `_NET_ACTIVE_WINDOW` as a single ClientMessage that means
"raise + focus this window". WMs treat this as a *request*. Every modern
WM has at least one mechanism that can drop the request silently. To
reliably raise+focus a window from a daemon you have to combine *all* of:

1. A real X server timestamp (not `X.CurrentTime`/0).
2. A `_NET_WM_USER_TIME` write on the target window with that timestamp.
3. The `_NET_ACTIVE_WINDOW` ClientMessage with `source_indication=2`
   (pager).
4. An explicit `ConfigureRequest` with `stack_mode=Above`.
5. An explicit `Display.flush()` so the WM sees the request immediately
   rather than at the next read.

Skipping any one of these breaks the operation on at least one mainstream
WM (Mutter, Muffin, KWin all check different subsets). See
`EwmhClient.activate_window` for the canonical sequence.

When Cinnamon is available, prefer `CinnamonAnimator.activate` (which
calls `mw.activate(global.get_current_time())` from inside the WM
process). Code running in-process is exempt from focus-stealing
prevention by construction; nothing in the X11 protocol is.

---

## Focus-stealing prevention is the default failure mode

When a request to focus / raise a window is dropped, you typically get
*no error*. The properties you'd read to check (`_NET_ACTIVE_WINDOW`,
`_NET_CLIENT_LIST_STACKING`) just don't update. From the user's POV the
hotkey "did nothing".

What WMs check (collected from Mutter / Muffin / KWin behaviour):

- **Timestamp staleness.** The request's timestamp is compared against
  the target window's `_NET_WM_USER_TIME` and against the WM's running
  notion of "current user time". A timestamp older than either gets the
  request rejected as focus-stealing. `X.CurrentTime` (= 0) is treated
  as "missing" and almost always rejected.
- **Source indication.** `source_indication=1` (application) gets the
  full focus-stealing-prevention treatment. `source_indication=2`
  (pager) softens *some* checks but does not bypass timestamp
  comparisons.
- **Recency window.** Even with a non-zero timestamp, if too much wall
  time has elapsed between the request being constructed and the WM
  processing it, the WM may treat it as stale. Buffering matters
  (next section).

Practical defences:

- Pass the **chord's** XI2 `data.time` through, not a timestamp captured
  later. The InputManager threads `data.time` from the chord callback
  all the way down to `EwmhClient.activate_window` for exactly this
  reason.
- Set `_NET_WM_USER_TIME` on the target to `timestamp - 1` *before*
  sending the activate. This pins the target as "less recently
  touched than our request" so the WM's user-time comparison can't
  fail. We're not lying about anything load-bearing — `_NET_WM_USER_TIME`
  on a window we're explicitly raising is the right place to declare
  "the user just wanted this window".
- Don't introduce new code paths that activate a window without a
  real timestamp. If you have one, pipe it through. If you genuinely
  don't (programmatic invocation, no recent input), accept that
  focus-stealing prevention may bite you.

---

## python-xlib does not auto-flush

`Window.send_event`, `Window.configure`, `Window.change_property` and
friends all *buffer* their requests in the connection's outgoing
buffer. The buffer is flushed only when:

- A read on the connection forces it (e.g. `next_event`, `pending_events`
  on an empty queue, `get_full_property`).
- You explicitly call `Display.flush()` or `Display.sync()`.

In an event-loop daemon, the next read often happens *much* later than
"right after the call you made" — the loop is parked in `select()` until
some event arrives, which can be the user releasing their chord several
hundred milliseconds later. By the time the WM sees your activate, its
notion of current time has advanced past the timestamp you sent and
focus-stealing prevention rejects it.

**Rule:** If you send a ClientMessage, ConfigureRequest, or property
write that you need the WM to process *now*, call `Display.flush()`
after the last write. The activation primitive in `EwmhClient` does
this; new code paths that bypass it should do the same.

`grab_server` blocks already end in `flush()` — that's why tile actions
inside `_grabbed()` work without each individual write needing one.

---

## `_NET_ACTIVE_WINDOW` and `_NET_CLIENT_LIST_STACKING` update at different
rates

After the WM processes our activate:

- `_NET_CLIENT_LIST_STACKING` reorders promptly (within the next
  redraw, typically <16ms).
- `_NET_ACTIVE_WINDOW` may lag for **multiple seconds** on Muffin even
  when the activate is honoured. We've seen 6+ second lags in
  production logs where the stacking property was already up to date.

Consequence: do not anchor "what is currently the visible top window"
on `_NET_ACTIVE_WINDOW`. Read `_NET_CLIENT_LIST_STACKING` and use
`stack[-1]` (or the top-most window matching some predicate). The cycle
implementation does this — anchoring on the active window produced an
"every other press is a no-op" pattern because the stale active
property kept reporting the deposed window.

If you genuinely need to know "what's focused" rather than "what's on
top", use `_NET_ACTIVE_WINDOW` with the understanding it lags. For
"what's visible at the top of this zone", use the stacking property.

---

## `classify_zone`'s 128 px tolerance is for *detection*, not *identity*

`zones.py:_ZONE_DEVIATION = 128` is the maximum drift accepted when
asking "is this window currently sitting in tile zone X?". 128 px is
deliberately generous because:

- GTK windows include their drop shadow in the X11 client rect via
  `_GTK_FRAME_EXTENTS` — typically 16–32 px on each side.
- Server-side decorated windows have their titlebar / borders
  *outside* the X11 client rect via `_NET_FRAME_EXTENTS`. Different
  WMs use different border widths.
- Different toolkits round geometry differently.

The tolerance tolerates all of those without a tighter check.

What 128 px is **not** for:

- "Are these two windows the same shape?" Two windows tiled to the
  same zone may legitimately have geometries that differ by close to
  the full 128 px (e.g. a calculator with a tall built-in titlebar
  versus a CSD terminal). They both belong in the cycle bucket.
- "Is this window really at the canonical zone?" There is no "really".
  The user can drag a window to a position that's 100 px off from the
  zone and we'll classify it as in the zone, because that's still
  closer to the zone than to anywhere else.

Don't add tighter geometry checks to filter "ghost" windows from the
cycle. If you find a window that classifies as zone X but you don't
think it should — the answer is almost always either:

1. It actually belongs (different frame extents, different toolkit).
2. It should be filtered by *type* (`_NET_WM_WINDOW_TYPE`), not by
   geometry distance.
3. It should be filtered by *visibility* (`map_state`).

---

## `_NET_CLIENT_LIST_STACKING` is a kitchen sink

The property contains every "client window" the WM tracks, including:

- Minimized / iconified windows (still in the list, but
  `map_state == IsUnmapped`).
- Dialogs, tooltips, dock windows, panels, notifications,
  splash screens, dropdown menus.
- Override-redirect windows in some WM implementations.
- Sticky windows on every desktop (`_NET_WM_DESKTOP == 0xFFFFFFFF`).

If you're walking this list for a user-facing feature ("cycle through
my windows"), filter aggressively. The cycle uses three filters:

1. `_NET_WM_WINDOW_TYPE_NORMAL` (or unset) — drops dialogs, dock,
   menus, tooltips, notifications, splashes.
2. `map_state == IsViewable` — drops minimized / withdrawn windows.
3. Same desktop or sticky.

Add new filters in `EwmhClient` so they're testable in isolation; don't
inline them.

---

## Cinnamon path vs X11 path

Two raise/activate paths exist and they're not interchangeable:

- **Cinnamon path** (`CinnamonAnimator.activate` and `animate`): runs
  JavaScript inside Cinnamon's process via D-Bus `Eval`. Calls
  `mw.activate(global.get_current_time())` — Mutter's internal API.
  Bypasses focus-stealing prevention because the call is *the WM
  itself* asking. Available only when `[cinnamon]` extra is
  installed.
- **X11 path** (`EwmhClient.activate_window`): the EWMH client-message
  approach with all the timestamp / user-time / flush armour
  documented above. Works on any EWMH-compliant WM.

The tile and cycle code both prefer the Cinnamon path and fall back to
the X11 path. When adding a new feature that activates a window, follow
this pattern — `Cinnamon.activate(xid)` is one D-Bus call, the
fallback is `props.activate_window(win, timestamp)`. Don't write a
third path.

The Cinnamon JS bakes the activate into the same script as
`move_resize_frame` for tile animations — atomic from Mutter's POV.
Do not follow up with a redundant X11 raise; ConfigureRequest stack
reordering can race with Mutter's own processing.

---

## Checklist for new "activate / raise" code paths

If you're adding a feature that needs to activate, raise, or focus a
window:

1. Do you have a real X server timestamp from the user input that
   triggered the action? (XI2 `data.time` for chord-driven actions.)
   If not, you'll need to thread one through or accept that focus-
   stealing prevention may reject you.
2. Are you reading `_NET_ACTIVE_WINDOW` to decide what to act on? If
   yes, expect it to lag after activates. Anchor on
   `_NET_CLIENT_LIST_STACKING` if your decision is about "what's on
   top right now".
3. Do you have a Cinnamon path? If your action is on a single window
   you can identify by xid, route through `CinnamonAnimator.activate`
   first.
4. If you're sending writes outside `_grabbed()`, do they need to land
   immediately? If yes, `Display.flush()` after the last write.
5. Does your candidate filter walk `_NET_CLIENT_LIST_STACKING`? If
   yes, apply at least the NORMAL + viewable + desktop filters.
6. If you're tempted to tighten `_ZONE_DEVIATION` or add a geometry
   distance check to filter "wrong" windows: re-read the
   `classify_zone` section above. The fix is usually a window-*type*
   filter, not a geometry tolerance.

---

## Why this is harder than it should be

The EWMH spec describes a cooperative model — clients ask the WM
politely, the WM honours sensible requests. Real-world WMs implement
focus-stealing prevention in different ways with different defaults,
because real-world apps frequently *abuse* the polite asking model
to steal focus. A daemon that wants to drive window state from a
hotkey ends up needing to look more legitimate than most apps:
threading user-input timestamps, claiming pager source-indication,
explicitly marking the target window as user-touched, flushing
writes immediately so the WM can't time them out. Each piece is
defensive against a different class of WM-side rejection. The pieces
look redundant in isolation; they aren't.
