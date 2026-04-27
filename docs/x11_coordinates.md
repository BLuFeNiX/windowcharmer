# X11 Window Coordinate System

This document explains how window positions are resolved in windowcharmer,
specifically the behavior of `translate_coords` and why the code in
`zones.py::get_window_position` looks the way it does.

---

## The Core Inversion

`window.translate_coords(src_window, x, y)` answers the question:
> "Where does the point (x, y) in `src_window`'s coordinate space
> fall in `window`'s coordinate space?"

When called as `window.translate_coords(root, 0, 0)`, you are asking:
> "Where does the root window's origin (0, 0) fall within this window?"

If a window's top-left corner is at screen position **(X, Y)**, then the
root's origin is **X pixels to the left and Y pixels above** the window's
own origin. The result is therefore:

```
coords.x = -X
coords.y = -Y
```

To recover the screen position:

```python
screen_x = -coords.x   # equivalently: abs(coords.x) when X >= 0
screen_y = -coords.y   # equivalently: abs(coords.y) when Y >= 0
```

---

## Why `abs()` in `determine_tile_zone`

```python
# zones.py — inside determine_tile_zone
geom = window.get_geometry()
coords = window.translate_coords(geom.root, 0, 0)
...
x, y = abs(coords.x), abs(coords.y)
```

The `abs()` calls are **correct and necessary**. For any window at a
non-negative screen position, `coords.x` is negative. Without `abs()`,
zone comparisons would receive large negative numbers and no zone would
ever match.

`abs()` is equivalent to `-coords.x` for visible windows. The sign is
what makes the difference: with `abs()`, a window at screen x=3968
produces 3968; without it, the zone check would see -3968.

---

## GTK CSD vs Non-CSD Windows

The coordinate that `translate_coords` returns refers to the **X11 window
object's** top-left corner. What that corner represents differs by window
type.

### GTK Client-Side Decorations (CSD)

GTK CSD windows include shadows and invisible borders *inside* the X11
window geometry. `_GTK_FRAME_EXTENTS` describes how large those borders
are on each side.

```
X11 window top-left (what translate_coords gives)
│
↓
┌──────────────────────────────────┐  ← X11 window boundary
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│  ← shadow/border (GTK_FRAME_EXTENTS.top)
│░┌─────────────────────────────┐░░│
│░│   visible window content    │░░│
│░│                             │░░│
│░└─────────────────────────────┘░░│
│░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│
└──────────────────────────────────┘
```

To find where the **visible content** starts on screen:

```python
gtk_fe = ...  # _GTK_FRAME_EXTENTS (left, right, top, bottom)
screen_x = abs(coords.x) + gtk_fe.left
screen_y = abs(coords.y) + gtk_fe.top
```

### Non-CSD Windows (traditional WM decorations)

The WM draws the titlebar **outside** the X11 client window. The client
window's top-left is directly the top of the content area.
`_NET_FRAME_EXTENTS` describes the WM frame extending *outside* the client.

```
WM frame top (titlebar) = abs(coords.y) - net_fe.top
Client window top-left  = abs(coords.x), abs(coords.y)  ← what translate_coords gives
```

---

## Verified Examples (5120×1440, workarea y=24 h=1416)

These were captured on the development machine to confirm the math.

| Window | translate_coords | Frame extents | Screen position | Interpretation |
|--------|-----------------|---------------|-----------------|----------------|
| Terminal (left) | cx=0, cy=−56 | NET t=32 | x=0, y=56 | Client at (0,56); titlebar at y=24 (=wa_y) |
| Terminal (right) | cx=−3968, cy=−56 | NET t=32 | x=3968, y=56 | x_right=3968; right edge=3968+1152=5120 |
| Firefox (center) | cx=−1136, cy=−14 | GTK l=16 t=10 | x=1136, y=14 | Visible at (1152,24); 1136+16=1152=x_center |

All three windows are fully tiled. The numbers verify that:
- `side_width = 1152`, `center_width = 2816`, `screen_width = 5120`
- `x_left = 0`, `x_center = 1152`, `x_right = 3968`
- `wa_y = 24` (the visible top edge of every tiled window)

---

## Zone Detection

`determine_tile_zone` in `zones.py` calls `get_geometry` once to fetch
both the root reference (for `translate_coords`) and the window's
width/height, then compares position and size against `ScreenDimensions`
thresholds with a ±128 pixel deviation.

The deviation exists to tolerate:
- GTK shadow offsets (windows positioned a few pixels outside the exact zone boundary)
- Minor WM rounding

The zone string logic: v_pos and h_pos are combined as `f"{v_pos}-{h_pos}"`,
then `"full-"` is stripped so `"full-left"` becomes `"left"`, etc. Zones
with v_pos=`'full'` span the entire workarea height.

---

## Common Pitfalls

1. **Do not negate coords.x/y yourself** — use `abs()`, which already
   handles the sign correctly for all visible windows.

2. **Do not remove `abs()`** — the raw coords are negative for positive
   screen positions. Zone comparisons use positive thresholds.

3. **GTK frame extents are inside the X11 geometry** — `geom.width` and
   `geom.height` include the shadow. Visible size is
   `geom.width - fe.left - fe.right`.

4. **NET_FRAME_EXTENTS are outside the X11 client geometry** — they
   describe the WM-drawn frame surrounding the client, not included in
   `geom.width`/`geom.height`.

5. **`translate_coords` direction matters** — always call it as
   `window.translate_coords(root, 0, 0)`, not `root.translate_coords(window, 0, 0)`.
   The latter would return the window's own position directly, not its negation.
