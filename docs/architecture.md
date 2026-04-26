# WindowCharmer Architecture

## Overview

WindowCharmer is a daemon that:
1. Remaps Super_L ↔ Hyper_L at the X11 keymap level so it can intercept `Super+key` hotkeys.
2. Grabs specific key combinations on the root window.
3. Responds to hotkeys by repositioning and resizing the active window using `_NET_WM_STATE` and `XConfigureWindow`.

---

## Thread Model

The daemon runs four background threads plus the main thread:

| Context | Thread | Role |
|---------|--------|------|
| Main / KeyGrabber | main thread | Blocking event loop — grabs keys, dispatches actions |
| KeyMonitor | daemon thread | XRecord session — fires on every KeyPress/KeyRelease/MappingNotify |
| Sleep detector | daemon thread | Polls wall-clock drift to detect suspend/resume |
| Udev monitor | daemon thread | Watches `/dev/input/event*` for keyboard hotplug |

`WindowManager` is not a thread — it is called from the main thread under a mutex.

The udev and sleep monitors call `_schedule_rebind()` which debounces via `threading.Timer` before calling `KeyboardMapper.apply_super_hyper_swap()`.

The KeyGrabber calls `_on_mapping_notify()` directly (no debounce) when the X server reports a `MappingNotify` for keyboard changes.

---

## DisplayPool

Each X11 operation requires a dedicated `Display` connection because python-xlib connections are **not thread-safe**. `DisplayPool` maps a role name to a connection:

| Name | Owner | Purpose |
|------|-------|---------|
| `"wm"` | `WindowManager` | Window property reads + configure calls |
| `"mapper"` | `KeyboardMapper` | `change_keyboard_mapping`, `keysym_to_keycode` |
| `"grabber"` | `KeyGrabber` | `grab_key`, `next_event` event loop |
| `"monitor"` | `KeyMonitor` | XRecord context (separate display required by the protocol) |

Each named connection is accessed from exactly one thread throughout the daemon's lifetime.

---

## Keymap Swap State Machine

The daemon needs `Super+key` to work as a hotkey modifier *without* opening the DE application menu on every Super press. The solution:

1. **At startup**: swap Super_L ↔ Hyper_L in the X11 keymap. The physical Super key now sends `Hyper_L` keysym.
2. **Key grabbing**: grab `Mod4+<key>` on the root window. `Mod4` is bound to the physical Super key (now Hyper_L keysym) by the X server's modifier map.
3. **Bare Super tap**: `SuperPassthroughTracker` (via XRecord) watches for a Super press with no following key. When it detects a bare tap, it simulates a `Hyper_L` key press via XTest — which the DE sees as the key that *used to* be Super, and opens the menu.
4. **At shutdown / `--fix-keymap`**: restore the original keymap so the user's keyboard works normally.

State transitions:

```
[canonical]  →  apply_super_hyper_swap()  →  [swapped]
[swapped]    →  cleanup() / force_canonical()  →  [canonical]
```

`apply_super_hyper_swap()` is idempotent — it reads the current mapping first and no-ops if already swapped.

`force_canonical()` is used by `--fix-keymap`. It does the same canonical restoration without consulting the daemon's swap state, making it safe to run in any state.

---

## Tiling Logic

### Zone detection (`tiling/zones.py`)

`determine_tile_zone()` heuristically identifies which zone a window currently occupies by comparing its position and size against the expected zone geometry (from `ScreenDimensions`). A tolerance of `_ZONE_DEVIATION = 128` px handles GTK shadow offsets and minor rounding across WMs.

See [x11_coordinates.md](x11_coordinates.md) for why `abs()` is applied to translated coordinates.

### Action dispatch (`tiling/manager.py`)

`_apply_tile_action()` uses a data table `_TILE_SPEC: dict[TileAction, _ZoneSpec]` that maps each action to a set of attribute names on `ScreenDimensions` (x, y, width, height) and the desired maximization flags. This eliminates 11 near-identical action methods.

Before issuing `XConfigureWindow`, `move_and_resize()` clears both maximization flags (`_NET_WM_STATE_MAXIMIZED_VERT` and `_NET_WM_STATE_MAXIMIZED_HORZ`) because WMs ignore configure requests on maximized windows.

### Screen dimensions (`config/dimensions.py`)

`ScreenDimensions` computes all zone geometry from four parameters: `screen_width`, `wa_y` (workarea top), `wa_h` (workarea height), and `center_width`. Workarea values come from `_NET_WORKAREA`, which accounts for panels and docks.

`_update_state()` re-queries screen dimensions from the X server on every action so RandR resolution changes are reflected without a restart.

---

## Grab Server Scope

`execute_action()` grabs the X server only around the actual window modification (configure + send_client_message), not around the state-reading phase. This minimises the time all other X clients are blocked.

---

## Udev / Sleep Debouncing

Both the sleep detector and the udev monitor can fire multiple times in rapid succession. `_schedule_rebind()` cancels and restarts a `threading.Timer(0.25s)` on each call — the actual `apply_super_hyper_swap()` only runs once the burst settles.

The udev monitor filters to `ID_INPUT_KEYBOARD == '1'` so that plugging in a mouse or USB hub does not trigger a keymap rebind.
