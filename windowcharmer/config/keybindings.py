import os
import tomllib
import logging
from pathlib import Path
from .actions import TileAction

logger = logging.getLogger(__name__)

class KeyBindings:
    """Manages key bindings, loading user overrides from config.toml."""

    @staticmethod
    def get_defaults() -> dict[str, TileAction]:
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

    @staticmethod
    def load() -> dict[str, TileAction]:
        """Loads bindings: defaults overridden by user config."""
        bindings = KeyBindings.get_defaults()
        
        # Determine config path
        config_dir = os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config'))
        config_file = Path(config_dir) / 'windowcharmer' / 'config.toml'

        if not config_file.exists():
            return bindings

        try:
            with open(config_file, "rb") as f:
                config_data = tomllib.load(f)
            
            if "keybindings" in config_data:
                for key, action_str in config_data["keybindings"].items():
                    try:
                        # Convert the string action back into the Enum
                        action = TileAction(action_str)
                        bindings[key] = action
                    except ValueError:
                        logger.warning(f"Invalid action '{action_str}' in config.toml for key '{key}'. Ignoring.")
        
        except Exception as e:
            logger.error(f"Failed to load config file {config_file}: {e}")

        return bindings
