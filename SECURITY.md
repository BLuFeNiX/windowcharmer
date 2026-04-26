# Security

## What WindowCharmer Does to Your System

WindowCharmer is a keymap-modifying X11 daemon. Here is a plain summary of
what it does and doesn't do:

**It does:**
- Grab `Super+<key>` key combinations at the root X11 window (visible to all clients).
- Swap the `Super_L` and `Hyper_L` keysyms in the X11 keyboard mapping while running.
- Read active window properties (`_NET_ACTIVE_WINDOW`, `_NET_WM_STATE`, `_NET_WORKAREA`,
  `_GTK_FRAME_EXTENTS`) to position windows.
- Send `XConfigureWindow` and `_NET_WM_STATE` client messages to resize and move windows.
- Simulate key events (`XTest`) to forward bare Super taps to the desktop environment.
- Watch `/dev/input/event*` via `pyudev` to detect keyboard hotplug.
- Poll wall-clock drift to detect suspend/resume.

**It does not:**
- Read key content (only keycodes, not what you type).
- Store, transmit, or log any keystrokes.
- Open network connections.
- Write to disk beyond normal Python logging.
- Require root or elevated privileges — runs entirely as the logged-in user.
- Interact with Wayland (X11 only).

## Required Privileges

None beyond the normal desktop session. WindowCharmer runs as your user and
interacts only with the X server your session already has access to.

## Threat Model

WindowCharmer is intended for single-user desktop use. It is not hardened against
a malicious local user on the same X session, and it makes no security guarantees
in multi-seat or shared-display environments.

## Reporting Vulnerabilities

Please open an issue at <https://github.com/BLuFeNiX/windowcharmer/issues> with
the label `security`. For sensitive reports, use the email in `pyproject.toml`.
