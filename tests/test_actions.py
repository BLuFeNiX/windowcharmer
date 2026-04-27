from windowcharmer.config.actions import TileAction
from windowcharmer.tiling.manager import _TILE_SPEC


def test_all_tile_actions_round_trip() -> None:
    for action in TileAction:
        assert TileAction(action.value) == action


def test_tile_action_values_are_strings() -> None:
    for action in TileAction:
        assert isinstance(action.value, str)


def test_tile_spec_covers_all_dispatch_actions() -> None:
    # Actions handled specially (not via _TILE_SPEC) — adding a new TileAction
    # without updating this set or _TILE_SPEC would silently become a no-op.
    handled_outside_spec = {TileAction.BIGGER, TileAction.SMALLER, TileAction.EXIT}
    assert set(_TILE_SPEC) | handled_outside_spec == set(TileAction)
