# WindowCharmer

WindowCharmer is a three-column window tiler for ultra-wide monitors, designed for X11 desktop environments. Actively tested on Cinnamon (Muffin); may work on GNOME (Mutter) and KDE (KWin) — [compatibility reports welcome](https://github.com/BLuFeNiX/windowcharmer/issues).

> **X11 only.** Wayland does not support global key grabs or XTest — WindowCharmer cannot run on Wayland.

## Installation

### Direct from GitHub (recommended)

**With [uv](https://docs.astral.sh/uv/getting-started/installation/)**:

```sh
uv tool install git+https://github.com/BLuFeNiX/windowcharmer.git
```

**With pip:**

```sh
pip install git+https://github.com/BLuFeNiX/windowcharmer.git
```

Either installs the `windowcharmer` binary to `~/.local/bin/`. Ensure that directory is on your `PATH`. Pin to a specific tag or branch by appending `@<ref>` (e.g. `…@v2026.4.2`).

To upgrade later: `uv tool upgrade windowcharmer` (uv) or re-run the pip command (pip).

### From a local clone

```sh
git clone git@github.com:BLuFeNiX/windowcharmer.git
cd windowcharmer
uv tool install .                  # or: pip install -e .
```

### Cinnamon animations (optional)

To get smooth compositor-driven animations when tiling on Cinnamon, install the `cinnamon` extra. Without it, tiles snap into place instantly (the `--no-animate` behavior).

```sh
sudo apt install libgirepository-2.0-dev libcairo2-dev python3-dev   # build headers for PyGObject
uv tool install 'windowcharmer[cinnamon] @ git+https://github.com/BLuFeNiX/windowcharmer.git'
# or: pip install 'windowcharmer[cinnamon] @ git+https://github.com/BLuFeNiX/windowcharmer.git'
```

## Usage

With uv:

```sh
windowcharmer
```

With pip (activate the venv first):

```sh
source .venv/bin/activate
windowcharmer
```

For automatic startup on login, add `windowcharmer` to your desktop environment's autostart list, or use the [systemd unit](#systemd-user-service).

**Note: You do not need to remove conflicting DE keybindings.** WindowCharmer grabs keys exclusively while running, so its bindings take precedence.

---

## Default Keybindings

**The activation key is Super_L** (the left "Windows" key).

| Key | Action |
|-----|--------|
| `Up` | `max` — maximize |
| `Down` | `center` — center column |
| `Left` | `left` — left column |
| `Right` | `right` — right column |
| `space` | `restore` — unmaximize |
| Numpad `7` (`KP_Home`) | `top-left` |
| Numpad `8` (`KP_Up`) | `top-center` |
| Numpad `9` (`KP_Page_Up`) | `top-right` |
| Numpad `4` (`KP_Left`) | `left` |
| Numpad `5` (`KP_Begin`) | `center` |
| Numpad `6` (`KP_Right`) | `right` |
| Numpad `1` (`KP_End`) | `bottom-left` |
| Numpad `2` (`KP_Down`) | `bottom-center` |
| Numpad `3` (`KP_Page_Down`) | `bottom-right` |
| Numpad `0` (`KP_Insert`) | `restore` |
| Numpad `+` (`KP_Add`) | `bigger` — widen center column |
| Numpad `-` (`KP_Subtract`) | `smaller` — narrow center column |
| `BackSpace` | `exit` — stop the daemon |

---

## Configuration

Keybindings can be overridden in `~/.config/windowcharmer/config.toml` (respects `$XDG_CONFIG_HOME`):

```toml
[keybindings]
# Super+F1 → center window
F1 = "center"
# Super+F2 → tile left
F2 = "left"
```

Supported action strings:
`left`, `right`, `center`, `top-left`, `bottom-left`, `top-right`, `bottom-right`,
`top-center`, `bottom-center`, `max`, `restore`, `bigger`, `smaller`, `exit`

Key names follow X11 keysym convention (e.g. `Up`, `KP_Home`, `F1`, `space`). Invalid action strings or unknown key names are logged as warnings and skipped — the daemon keeps running with the remaining bindings.

---

## Keymap Notes

WindowCharmer remaps your keyboard while the daemon runs: it **swaps Super_L and Hyper_L** at the X11 level. This is how it intercepts `Super+<key>` without interfering with the desktop environment's own Super key handling.

- When you tap Super alone (no tiling key), WindowCharmer forwards a synthetic `Super_L` press at the keycode where the swap parked the `Super_L` keysym (the original Hyper position). Your DE's bare-Super hotkey — typically "Show application menu" — fires from that synthetic event, so bare-Super still opens your menu.
- On daemon exit the original mapping is restored automatically.
- If the daemon crashes mid-swap, run `windowcharmer --fix-keymap` to restore the canonical mapping without starting the daemon.

### If your keymap gets stuck

```sh
windowcharmer --fix-keymap
```

This reads the current Super_L/Hyper_L keycodes, assigns them back to their canonical keysyms, and exits. It is safe to run at any time.

---

## Systemd User Service

Choose the unit file that matches your install method.

**With uv** — copy and enable:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/windowcharmer.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now windowcharmer
```

**With pip** — edit the unit file to set your clone path first, then copy and enable:

```sh
CLONE_DIR="$HOME/windowcharmer"  # adjust if you cloned elsewhere
sed -i "s|/path/to/windowcharmer|$CLONE_DIR|" systemd/windowcharmer-venv.service
mkdir -p ~/.config/systemd/user
cp systemd/windowcharmer-venv.service ~/.config/systemd/user/windowcharmer.service
systemctl --user daemon-reload
systemctl --user enable --now windowcharmer
```

View logs:

```sh
journalctl --user -u windowcharmer -f
```

---

## Troubleshooting

**The daemon won't start / exits immediately**

- Run `windowcharmer` from a terminal to see error output.
- Check `journalctl --user -u windowcharmer` if using systemd.
- Ensure no other application has grabbed the same keys (another tiling daemon, DE snapping, etc.).

**Super key stops opening the application menu**

The keymap swap is likely stuck. Run `windowcharmer --fix-keymap` and then restart the daemon.

**Tiling doesn't snap to the right columns**

WindowCharmer reads `_NET_WORKAREA` to detect panel height. If your panel manager doesn't set this property, tile positions may be off. Check with `xprop -root _NET_WORKAREA`.

**The daemon starts but does nothing when I press Super**

Another client may have grabbed the key first. Look for competing grabs:
```sh
xev | grep -i key
```

---

## Support

For issues or questions, see the [issue tracker](https://github.com/BLuFeNiX/windowcharmer/issues).
