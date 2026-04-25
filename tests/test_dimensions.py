import pytest
from windowcharmer.config.dimensions import ScreenDimensions


@pytest.mark.parametrize(
    "screen_width, wa_y, wa_h, center_width",
    [
        (2560, 0, 1440, 853),   # 2560×1440, ~1/3 center (853*3=2559; 1px gap from //2)
        (3840, 0, 1600, 1536),  # 3840×1600, 40% center
        (5120, 40, 1400, 2048), # 5120×1440 with 40px panel, 40% center
        (2560, 0, 1440, 0),     # two-column mode (no center)
    ],
)
def test_column_adjacency(screen_width: int, wa_y: int, wa_h: int, center_width: int) -> None:
    dim = ScreenDimensions(screen_width, wa_y, wa_h, center_width)
    assert dim.x_left + dim.w_side == dim.x_center
    # Integer division of side_width may leave a 1-pixel gap on odd-remainder screens.
    assert abs(dim.x_center + dim.w_center - dim.x_right) <= 1


@pytest.mark.parametrize(
    "screen_width, wa_y, wa_h, center_width",
    [
        (2560, 0, 1440, 853),
        (5120, 40, 1400, 2048),
    ],
)
def test_vertical_split(screen_width: int, wa_y: int, wa_h: int, center_width: int) -> None:
    dim = ScreenDimensions(screen_width, wa_y, wa_h, center_width)
    assert dim.y_bottom - dim.y_top == dim.h_half
    assert dim.h_half * 2 == dim.h_full


def test_wa_y_offset() -> None:
    dim = ScreenDimensions(2560, 40, 1400, 0)
    assert dim.y_top == 40
    assert dim.y_bottom == 40 + 700
    assert dim.h_full == 1400
