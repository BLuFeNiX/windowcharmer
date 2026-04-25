# WindowCharmer

WindowCharmer is a three-column window tiler for ultra-wide monitors, designed to be compatible with the Cinnamon desktop environment. It can likely work on any X11 environment, but this has not been tested and may need tweaking.

## Installation

```sh
git clone git@github.com:BLuFeNiX/windowcharmer.git
cd windowcharmer
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

```sh
cd windowcharmer
source .venv/bin/activate
windowcharmer
```

You will likely want to run this automatically on login, which is an exercise left for the user, but [start_daemon.sh](start_daemon.sh) will work for most users by simply adding that file to your startup programs list.

**Note: Removing conflicting keybindings in your desktop environment (such as window snapping controls) should NOT be necessary. They will be overridden while the daemon is running.**

#### Default Keybindings

**The activation key is Super_L** (the left "Windows" key). Default bindings:

| Key | Action |
|-----|--------|
| `Up` | `max` — maximize |
| `Down` | `center` — center column |
| `Left` | `left` — left column |
| `Right` | `right` — right column |
| `space` | `restore` — unmaximize |
| `Numpad 7` (`KP_Home`) | `top-left` |
| `Numpad 8` (`KP_Up`) | `top-center` |
| `Numpad 9` (`KP_Page_Up`) | `top-right` |
| `Numpad 4` (`KP_Left`) | `left` |
| `Numpad 5` (`KP_Begin`) | `center` |
| `Numpad 6` (`KP_Right`) | `right` |
| `Numpad 1` (`KP_End`) | `bottom-left` |
| `Numpad 2` (`KP_Down`) | `bottom-center` |
| `Numpad 3` (`KP_Page_Down`) | `bottom-right` |
| `Numpad 0` (`KP_Insert`) | `restore` |
| `Numpad +` (`KP_Add`) | `bigger` — widen center column |
| `Numpad -` (`KP_Subtract`) | `smaller` — narrow center column |
| `BackSpace` | `exit` — stop the daemon |

For example, to tile the window to the left, press `Super` and the `left arrow` key. To kill the daemon, press `Super+backspace`.

#### Configuration

Keybindings can be overridden in `~/.config/windowcharmer/config.toml` (respects `$XDG_CONFIG_HOME`):

```toml
[keybindings]
# Map Super+F1 to center the window
F1 = "center"
# Map Super+F2 to tile left
F2 = "left"
```

Supported action strings (all values of the `TileAction` enum):
`left`, `right`, `center`, `top-left`, `bottom-left`, `top-right`, `bottom-right`,
`top-center`, `bottom-center`, `max`, `restore`, `bigger`, `smaller`, `exit`

Key names follow the X11 keysym naming convention (e.g. `Up`, `KP_Home`, `F1`, `space`).

## Support

For issues, questions, or contributions, please refer to the [issue tracker](https://github.com/BLuFeNiX/windowcharmer/issues).
