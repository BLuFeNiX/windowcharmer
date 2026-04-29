# WindowCharmer Configuration

## Config File Location

WindowCharmer reads an optional TOML config file at:

```
$XDG_CONFIG_HOME/windowcharmer/config.toml
```

`$XDG_CONFIG_HOME` defaults to `~/.config` when unset, so the typical path is:

```
~/.config/windowcharmer/config.toml
```

| Distribution | Typical `XDG_CONFIG_HOME` |
|---|---|
| Most Linux distros | `~/.config` |
| Flatpak sandboxed | `~/.var/app/<app-id>/config` |

If the file doesn't exist, WindowCharmer starts with defaults and no error is logged.

---

## [keybindings]

All configuration lives under the `[keybindings]` section. Unknown top-level sections are logged as warnings.

```toml
[keybindings]
# Super+F1 → center window
F1 = "center"
# Super+F2 → tile left
F2 = "left"
```

Keys are X11 keysym names (case-sensitive). Invalid action strings or unknown key names are logged as warnings and skipped — the daemon keeps running with the remaining bindings.

### Supported Actions

| Action string | Description |
|---|---|
| `left` | Tile to the left column (full height) |
| `right` | Tile to the right column (full height) |
| `center` | Tile to the center column (full height) |
| `top-left` | Top half of the left column |
| `bottom-left` | Bottom half of the left column |
| `top-right` | Top half of the right column |
| `bottom-right` | Bottom half of the right column |
| `top-center` | Top half of the center column |
| `bottom-center` | Bottom half of the center column |
| `max` | Maximize the window |
| `restore` | Unmaximize / restore |
| `bigger` | Widen the center column one step |
| `smaller` | Narrow the center column one step |
| `cycle` | Alt-tab among windows that share the focused window's zone (or, if the focused window is floating, among other floating windows). A fresh chord activates the second-from-top bucket member; holding Super and pressing the chord again walks the cursor deeper through the bucket; releasing Super resets so the next press starts from second-from-top again |
| `exit` | Stop the WindowCharmer daemon |

### Supported Key Names

Key names follow the X11 keysym convention. Common examples:

- Arrow keys: `Up`, `Down`, `Left`, `Right`
- Function keys: `F1` through `F24`
- Numpad: `KP_Home`, `KP_Up`, `KP_Page_Up`, `KP_Left`, `KP_Begin`, `KP_Right`,
  `KP_End`, `KP_Down`, `KP_Page_Down`, `KP_Insert`, `KP_Add`, `KP_Subtract`
- Other: `space`, `BackSpace`, `Return`, `Tab`

For a full list, see `xev` output or the X11 keysym header (`/usr/include/X11/keysymdef.h`).

### Parse Error Behavior

| Situation | Behavior |
|---|---|
| File does not exist | Start with defaults, no warning |
| TOML syntax error | Log error, start with defaults |
| Unknown section (e.g. `[keybinding]`) | Log warning, rest of file still parsed |
| Unknown key name | Log warning, binding skipped |
| Unknown action string | Log warning, binding skipped |

---

## Center-Column Ratio

The center column width cycles through these ratios (relative to screen width):

| Ratio index | Width |
|---|---|
| 0 | 0 % (two-column mode — center actions map to left) |
| 1 | ~33 % |
| 2 | 40 % |
| 3 | 45 % |
| 4 | 50 % (default) |
| 5 | 55 % |
| 6 | 60 % |
| 7 | 65 % |

`bigger` / `smaller` step through this list. The ratio is **per virtual desktop** and resets to the default (50 %) on daemon restart.
