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
    """Return the built-in Super+key defaults."""
    # fmt: off
    return {
        'Up':           TileAction.MAX,
        'Down':         TileAction.CENTER,
        'Left':         TileAction.LEFT,
        'Right':        TileAction.RIGHT,
        'space':        TileAction.RESTORE,
        'Tab':          TileAction.CYCLE,

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


def get_default_shift_keybindings() -> dict[str, TileAction]:
    """Return the built-in Super+Shift+key defaults.

    Focus actions only — they walk the stack for the front-most window
    in the named zone group (left covers left + left-center + top-left
    + bottom-left, etc.). Numpad aliases mirror the arrow keys.
    """
    # fmt: off
    return {
        'Left':     TileAction.FOCUS_LEFT,
        'Right':    TileAction.FOCUS_RIGHT,
        'Down':     TileAction.FOCUS_CENTER,

        'KP_Left':  TileAction.FOCUS_LEFT,
        'KP_Right': TileAction.FOCUS_RIGHT,
        'KP_Down':  TileAction.FOCUS_CENTER,
        'KP_Begin': TileAction.FOCUS_CENTER,
    }
    # fmt: on


_SECTION_NO_SHIFT = "keybindings"
_SECTION_SHIFT = "shift_keybindings"
_KNOWN_SECTIONS = frozenset({_SECTION_NO_SHIFT, _SECTION_SHIFT})


def _apply_overrides(bindings: dict[str, TileAction], section: object, section_name: str) -> None:
    """Apply user overrides from a config section onto the defaults.

    Logs and ignores invalid action strings or non-table sections.
    """
    if not isinstance(section, dict):
        if section is not None:
            logger.warning("[%s] in config.toml must be a table — ignoring.", section_name)
        return
    for key, action_str in section.items():
        try:
            bindings[key] = TileAction(action_str)
        except ValueError:
            logger.warning("Invalid action '%s' for key '%s' in [%s] — ignoring.", action_str, key, section_name)


def load_keybindings() -> tuple[dict[str, TileAction], dict[str, TileAction]]:
    """Return (super, super+shift) bindings, defaults overridden by config.toml.

    Both tables are loaded in one parse so unknown-section warnings fire
    exactly once. Each table maps an X11 keysym name to a TileAction;
    the InputManager registers a passive grab for every entry under its
    respective modifier mask.
    """
    no_shift = get_default_keybindings()
    shift = get_default_shift_keybindings()

    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else _XDG_DEFAULT
    config_file = config_home / "windowcharmer" / "config.toml"

    if not config_file.exists():
        return no_shift, shift

    try:
        with config_file.open("rb") as f:
            config_data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error("Failed to load config file %s: %s", config_file, e)
        return no_shift, shift

    for section in config_data:
        if section not in _KNOWN_SECTIONS:
            logger.warning("Unknown section [%s] in config.toml — ignoring.", section)

    _apply_overrides(no_shift, config_data.get(_SECTION_NO_SHIFT), _SECTION_NO_SHIFT)
    _apply_overrides(shift, config_data.get(_SECTION_SHIFT), _SECTION_SHIFT)

    return no_shift, shift
