import logging
import tomllib

from platformdirs import user_config_path

from .actions import TileAction

logger = logging.getLogger(__name__)


def get_default_keybindings() -> dict[str, TileAction]:
    """Return the built-in keybinding defaults."""
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

        'KP_Prior':     TileAction.TOP_RIGHT,
        'KP_Next':      TileAction.BOTTOM_RIGHT,

        'KP_Add':       TileAction.BIGGER,
        'KP_Subtract':  TileAction.SMALLER,

        'BackSpace':    TileAction.EXIT,
    }


def load_keybindings() -> dict[str, TileAction]:
    """Return defaults overridden by ~/.config/windowcharmer/config.toml if present."""
    bindings = get_default_keybindings()

    config_file = user_config_path("windowcharmer") / "config.toml"

    if not config_file.exists():
        return bindings

    try:
        with open(config_file, "rb") as f:
            config_data = tomllib.load(f)

        for key, action_str in config_data.get("keybindings", {}).items():
            try:
                bindings[key] = TileAction(action_str)
            except ValueError:
                logger.warning(f"Invalid action '{action_str}' for key '{key}' in config.toml — ignoring.")

    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.error(f"Failed to load config file {config_file}: {e}")

    return bindings
