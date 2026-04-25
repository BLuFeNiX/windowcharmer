from windowcharmer.config.actions import TileAction


def test_all_tile_actions_round_trip() -> None:
    for action in TileAction:
        assert TileAction(action.value) == action


def test_tile_action_values_are_strings() -> None:
    for action in TileAction:
        assert isinstance(action.value, str)
