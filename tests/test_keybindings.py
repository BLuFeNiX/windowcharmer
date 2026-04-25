import tomllib
import pytest
from pathlib import Path
from unittest.mock import patch, mock_open
from windowcharmer.config.keybindings import load_keybindings, get_default_keybindings
from windowcharmer.config.actions import TileAction


def test_defaults_load_without_config(tmp_path: Path) -> None:
    # tmp_path has no windowcharmer/ subdirectory → no config.toml → pure defaults
    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}):
        bindings = load_keybindings()
    assert bindings == get_default_keybindings()


def test_user_config_overrides_default(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[keybindings]\nF1 = "left"\n')

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        bindings = load_keybindings()

    assert bindings["F1"] == TileAction.LEFT


def test_invalid_action_is_skipped(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[keybindings]\nF1 = "not_a_real_action"\n')

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        bindings = load_keybindings()

    # Invalid action should be skipped; F1 should not be in defaults
    assert "F1" not in bindings


def test_malformed_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("this is not [ valid toml !!!")

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        bindings = load_keybindings()

    assert bindings == get_default_keybindings()
