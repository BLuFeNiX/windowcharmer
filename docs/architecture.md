# WindowCharmer Architecture

## Overview

WindowCharmer is a daemon that:
1. Remaps Super_L ↔ Hyper_L at the X11 keymap level so it can intercept `Super+key` hotkeys without disrupting the DE's own Super handling.
2. Grabs specific key combinations on the root window via XInput2 passive grabs.
3. Responds to hotkeys by repositioning and resizing the active window using `_NET_WM_STATE` and `XConfigureWindow`.

---

## Thread Model

The daemon runs one background thread plus the main thread:

| Context | Thread | Role |
|---------|--------|------|
| Main / InputManager | main thread | Blocking event loop — receives XI2 events, dispatches actions, handles hot-plug |
| Sleep detector | daemon thread | Polls wall-clock drift to detect suspend/resume |

`WindowManager` is not a thread — it is called from the main thread (the InputManager event loop), so its X11 connection has a single accessor.

The sleep monitor calls `_schedule_rebind()` which debounces via `threading.Timer` before calling `KeyboardMapper.apply_super_hyper_swap()`. The InputManager's `HierarchyChanged` handler (keyboard hot-plug) goes through the same path. Core `MappingNotify` is dispatched directly without debounce — the swap is idempotent so repeated calls collapse harmlessly.

Earlier versions ran a separate `KeyMonitor` thread (XRecord) and a separate `UdevKeyboardMonitor` thread (pyudev) for keyboard event capture and hot-plug detection respectively. Both responsibilities now live in the InputManager via XI2 raw events and `HierarchyChanged` events on a single Display.

---

## DisplayPool

Each X11 operation requires a dedicated `Display` connection because python-xlib connections are **not thread-safe**. `DisplayPool` maps a role name to a connection:

| Name | Owner | Purpose |
|------|-------|---------|
| `"wm"` | `WindowManager` | Window property reads + configure calls |
| `"mapper"` | `KeyboardMapper` | `change_keyboard_mapping`, key-event simulation |
| `"input"` | `InputManager` | XI2 select_events, passive grabs, event loop |

Each named connection is accessed from exactly one thread under normal operation. The `"mapper"` connection is touched from the main thread (apply_swap, cleanup) and the debounce timer thread (apply_swap, simulate_super_press); KeyboardMapper serializes those callers behind its own lock.

---

## Keymap Swap State Machine

The daemon needs `Super+key` to work as a hotkey modifier *without* opening the DE application menu on every Super press. The solution:

1. **At startup**: swap Super_L ↔ Hyper_L in the X11 keymap. The physical Super key now sends the `Hyper_L` keysym.
2. **Key grabbing**: passive-grab `Mod4+<key>` via `XIPassiveGrabDevice`. `Mod4` is bound by the X server's modifier map to the physical Super key's keycode (independent of which keysym lives there), so chord grabs still fire correctly.
3. **Bare Super tap**: `SuperPassthroughTracker` watches XI2 raw events for a Super press with no following key. On a bare tap, `simulate_super_press` injects a synthetic event at the keycode where the `Super_L` keysym now lives (the canonical Hyper position). The synthetic event arrives at the X server as a `Super_L` keypress — which is what triggers the DE's default "Show menu" hotkey.
4. **At shutdown / `--fix-keymap`**: restore the original keymap so the user's keyboard works normally.

State transitions:

```
[canonical]  →  apply_super_hyper_swap()           →  [swapped]
[swapped]    →  cleanup() / force_canonical()      →  [canonical]
```

`apply_super_hyper_swap()` is idempotent — it reads the current mapping first and no-ops if already swapped. This survives the MappingNotify echo from its own writes.

`force_canonical()` is used by `--fix-keymap`. It does the same canonical restoration without consulting the daemon's swap state, making it safe to run from any state.

---

## XI2 Input Layer

`InputManager` (`input/xi2_manager.py`) consolidates three responsibilities that used to live in separate modules and threads:

- **Tile-chord dispatch**: `XIPassiveGrabDevice` registers a passive grab for each configured chord. When the chord fires, an XI2 `KeyPress` event arrives via the grab's own `event_mask`, and the InputManager dispatches the matching action.
- **Bare-Super-tap detection**: XI2 `RawKeyPress` / `RawKeyRelease` events fire before focus dispatch and grab activation, so the daemon sees every keystroke regardless of which window is focused. These feed `SuperPassthroughTracker`. Without raw events, key events would only reach the daemon when the daemon's window was focused (never, in practice).
- **Keyboard hot-plug**: `XIHierarchyChangedMask` delivers `HierarchyChanged` events when a device is added, removed, enabled, or disabled. A keyboard hot-plug triggers `_schedule_rebind()` to re-apply the swap on the new device's keymap.

### Synthetic event filtering

`simulate_super_press` injects events via `xtest.fake_input`. These propagate back through XI2 raw events with the same shape as real input. To avoid an infinite feedback loop (the synthetic press would re-trigger the bare-tap detector and call simulate again), the InputManager filters events whose XI2 `sourceid` is in the set of XTEST virtual devices.

XTEST devices are identified at startup via the `XI_PROP_XTEST_DEVICE` property — a read-only property the X server attaches to every XTEST slave (defined in xserver-properties.h). The set is re-enumerated on every `HierarchyChanged` so MPX (multi-pointer X) setups correctly track XTEST slaves added at runtime.

### Master-echo suppression

XI2 raw events selected on `AllDevices` deliver from BOTH the originating slave (`deviceid==sourceid`) and the master keyboard the slave is attached to (`deviceid=master, sourceid=slave`) — meaning a single physical press would reach the tracker twice. The InputManager filters to slave-originated events only (`deviceid == sourceid`).

---

## Tiling Logic

### Zone detection (`tiling/zones.py`)

`determine_tile_zone()` heuristically identifies which zone a window currently occupies by comparing its position and size against the expected zone geometry (from `ScreenDimensions`). A tolerance of `_ZONE_DEVIATION = 128` px handles GTK shadow offsets and minor rounding across WMs.

See [x11_coordinates.md](x11_coordinates.md) for why `abs()` is applied to translated coordinates.

### Action dispatch (`tiling/manager.py`)

`_apply_tile_action()` uses a data table `_TILE_SPEC: dict[TileAction, _ZoneSpec]` mapping each action to a geometry lambda over `ScreenDimensions` plus optional flag-only operations. Eliminates 11 near-identical action methods.

Before issuing `XConfigureWindow`, `move_and_resize()` clears `_NET_WM_STATE_MAXIMIZED_*` and `_NET_WM_STATE_FULLSCREEN` because WMs ignore configure requests on maximized or fullscreen windows.

### Screen dimensions (`config/dimensions.py`)

`ScreenDimensions` computes all zone geometry from the workarea rectangle (`_NET_WORKAREA`) and `center_width`. Workarea values account for panels and docks.

`_update_state()` re-queries screen dimensions on every action so RandR resolution changes are reflected without a restart.

---

## Grab Server Scope

`execute_action()` grabs the X server only around the actual window modification (configure + send_client_message), not around the state-reading phase. This minimises the time all other X clients are blocked.

The animated path runs *outside* `grab_server` because Cinnamon's compositor is a separate X11 client and would deadlock against our grab.
