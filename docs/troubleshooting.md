# WindowCharmer Troubleshooting

## Daemon won't start / exits immediately

1. Run from a terminal to see error output:
   ```sh
   windowcharmer --debug
   ```
2. Check systemd logs:
   ```sh
   journalctl --user -u windowcharmer -f
   ```
3. Verify no other application has grabbed the same keys:
   ```sh
   xev | grep -i key
   ```

---

## BadAccess: another client already owns the grab

WindowCharmer logs:
> `InputManagerError: BadAccess during grab: ...`

**Cause**: another program has grabbed the same `Super+<key>` combination.
Common culprits: other tiling managers, Compiz, picom with key-binding plugins,
or the DE's own snap / tile shortcuts.

**Fix**: disable the conflicting grab in the other application, then restart WindowCharmer.

You can identify which client holds a grab with:
```sh
xdotool getactivewindow
# or check all active grabs (requires xgrabinfo or similar)
```

---

## Super key stops opening the application menu

The keymap swap is stuck. Run:
```sh
windowcharmer --fix-keymap
```

Then restart the daemon. If the problem persists across reboots, check whether another daemon is re-applying a conflicting keymap (e.g. `xcape`, `xmodmap` rules in `~/.xinitrc`).

---

## Tiling doesn't snap to the right columns

WindowCharmer reads `_NET_WORKAREA` to detect panel height and position. If your panel manager doesn't set this property, tile positions will use the full screen height (ignoring panels).

Diagnose with:
```sh
xprop -root _NET_WORKAREA
```

If absent, configure your panel manager to advertise `_NET_WORKAREA` (most EWMH-compliant panels do this automatically), or file an issue.

---

## No tiling on bare WMs (i3, dwm, bspwm)

`_NET_WORKAREA` is typically not set on tiling WMs without a compositor. WindowCharmer will fall back to the full screen height, which is usually correct on bare setups with no panel.

Additionally, `_NET_CURRENT_DESKTOP` may be absent on some minimal WMs — WindowCharmer logs a one-time warning and defaults to desktop 0.

---

## Stuck modifier keys after crash

If WindowCharmer crashes mid-keymap-swap, the Super key may no longer open the application menu (it now sends Hyper_L instead). Fix with:
```sh
windowcharmer --fix-keymap
```

---

## XInput2 / passthrough errors

If you see:
> `InputManagerError: XInputExtension not present on this server`

Your X server doesn't have the XInput extension loaded. WindowCharmer requires XInput2 ≥ 2.0 for both key dispatch and the bare-Super passthrough.

On Xorg: XInput is usually compiled in. Check with:
```sh
xdpyinfo | grep -i xinput
```

If you see:
> `XISelectEvents failed: ...`

The X server rejected our event-mask selection. Most often this surfaces as a confusing `BadRRModeError` (a python-xlib quirk: its bundled `randr.py` registers `BadRRModeError` at the same numeric error code as core `BadValue`, so any `BadValue` on a system with RandR loaded is misreported as `BadRRMode`). The underlying error is almost always a malformed XISelectEvents call — open an issue with the full debug log if you hit this.

---

## Debug logging

Pass `--debug`:
```sh
windowcharmer --debug
```

Debug output includes: keycode resolution, MappingNotify events, zone detection results, and every tiling action dispatched.
