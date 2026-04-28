import logging
import os
import tomllib
from pathlib import Path

from .actions import TileAction

# XDG Base Directory Spec: $XDG_CONFIG_HOME, falling back to ~/.config when
# unset OR empty (XDG treats empty as unset).
_XDG_DEFAULT = Path.home() / ".config"

logger = logging.getLogger(__name__)


def get_default_keybindings() -> dict[str, TileAction]:
    """Return the built-in keybinding defaults."""
    # fmt: off
    return {
        'Up':           TileAction.MAX,
        'Down':         TileAction.CENTER,
        'Left':         TileAction.LEFT,
        'Right':        TileAction.RIGHT,
        'space':        TileAction.RESTORE,

        'KP_Home':      TileAction.TOP_LEFT,
        'KP_Up':        TileAction.TOP_CENTER,
        'KP_Page_Up':   TileAction.TOP_RIGHT,
        'KP_Left':      TileAction.LEFT,
        'KP_Begin':     TileAction.CENTER,
        'KP_Right':     TileAction.RIGHT,
        'KP_End':       TileAction.BOTTOM_LEFT,
        'KP_Down':      TileAction.BOTTOM_CENTER,
        'KP_Page_Down': TileAction.BOTTOM_RIGHT,
        'KP_Insert':    TileAction.RESTORE,

        # KP_Prior / KP_Next are the historical keysym names of KP_Page_Up /
        # KP_Page_Down. Some X servers register only one of the two names;
        # binding both keeps the numpad working in those cases.
        'KP_Prior':     TileAction.TOP_RIGHT,
        'KP_Next':      TileAction.BOTTOM_RIGHT,

        'KP_Add':       TileAction.BIGGER,
        'KP_Subtract':  TileAction.SMALLER,

        'BackSpace':    TileAction.EXIT,
    }
    # fmt: on


def load_keybindings() -> dict[str, TileAction]:
    """Return defaults overridden by ~/.config/windowcharmer/config.toml if present."""
    bindings = get_default_keybindings()

    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else _XDG_DEFAULT
    config_file = config_home / "windowcharmer" / "config.toml"

    if not config_file.exists():
        return bindings

    try:
        with config_file.open("rb") as f:
            config_data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error("Failed to load config file %s: %s", config_file, e)
        return bindings

    for section in config_data:
        if section != "keybindings":
            logger.warning("Unknown section [%s] in config.toml — ignoring.", section)

    section = config_data.get("keybindings", {})
    if not isinstance(section, dict):
        logger.warning("[keybindings] in config.toml must be a table — ignoring.")
        return bindings

    for key, action_str in section.items():
        try:
            bindings[key] = TileAction(action_str)
        except ValueError:
            logger.warning("Invalid action '%s' for key '%s' in config.toml — ignoring.", action_str, key)

    return bindings
