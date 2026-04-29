from pathlib import Path
from unittest.mock import patch

from windowcharmer.config.actions import TileAction
from windowcharmer.config.keybindings import (
    get_default_keybindings,
    get_default_shift_keybindings,
    load_keybindings,
)


def test_defaults_load_without_config(tmp_path: Path) -> None:
    # tmp_path has no windowcharmer/ subdirectory → no config.toml → pure defaults
    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}):
        no_shift, shift = load_keybindings()
    assert no_shift == get_default_keybindings()
    assert shift == get_default_shift_keybindings()


def test_default_tab_binds_cycle() -> None:
    """Super+Tab is wired to CYCLE by default."""
    assert get_default_keybindings()["Tab"] == TileAction.CYCLE


def test_default_shift_arrows_focus_tiled() -> None:
    """Super+Shift+Left/Right/Down focus the front-most tile in that group."""
    shift = get_default_shift_keybindings()
    assert shift["Left"] == TileAction.FOCUS_LEFT
    assert shift["Right"] == TileAction.FOCUS_RIGHT
    assert shift["Down"] == TileAction.FOCUS_CENTER


def test_user_config_overrides_default(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[keybindings]\nF1 = "left"\n')

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        no_shift, _ = load_keybindings()

    assert no_shift["F1"] == TileAction.LEFT


def test_user_config_overrides_shift_section(tmp_path: Path) -> None:
    """Super+Shift+key overrides go in a separate [shift_keybindings] table."""
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[shift_keybindings]\nF2 = "focus-right"\n')

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        _, shift = load_keybindings()

    assert shift["F2"] == TileAction.FOCUS_RIGHT


def test_invalid_action_is_skipped(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('[keybindings]\nF1 = "not_a_real_action"\n')

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        no_shift, _ = load_keybindings()

    # Invalid action should be skipped; F1 should not be in defaults
    assert "F1" not in no_shift


def test_malformed_toml_falls_back_to_defaults(tmp_path: Path) -> None:
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("this is not [ valid toml !!!")

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        no_shift, shift = load_keybindings()

    assert no_shift == get_default_keybindings()
    assert shift == get_default_shift_keybindings()


def test_non_dict_keybindings_section_falls_back_to_defaults(tmp_path: Path) -> None:
    """A top-level `keybindings = "foo"` (not a table) must not crash the daemon."""
    config_dir = tmp_path / ".config" / "windowcharmer"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('keybindings = "foo"\n')

    with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path / ".config")}):
        no_shift, shift = load_keybindings()

    assert no_shift == get_default_keybindings()
    assert shift == get_default_shift_keybindings()
