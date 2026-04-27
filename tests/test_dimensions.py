import pytest

from windowcharmer.config.dimensions import ScreenDimensions


@pytest.mark.parametrize(
    "screen_width, wa_y, wa_h, center_width",
    [
        (2560, 0, 1440, 853),  # 2560x1440, ~1/3 center (853*3=2559; 1px gap from //2)
        (3840, 0, 1600, 1536),  # 3840x1600, 40% center
        (5120, 40, 1400, 2048),  # 5120x1440 with 40px panel, 40% center
        (2560, 0, 1440, 0),  # two-column mode (no center)
    ],
)
def test_column_adjacency(screen_width: int, wa_y: int, wa_h: int, center_width: int) -> None:
    dim = ScreenDimensions(screen_width, wa_y, wa_h, center_width)
    assert dim.x_left + dim.w_side == dim.x_center
    assert dim.x_center + dim.w_center == dim.x_right


@pytest.mark.parametrize(
    "screen_width, center_width",
    [
        (1921, 768),  # 3-column odd width
        (1920, 768),  # 3-column even width
        (1921, 0),  # 2-column odd width — leftover sits in w_center (unused)
    ],
)
def test_columns_cover_full_width(screen_width: int, center_width: int) -> None:
    dim = ScreenDimensions(screen_width, 0, 1080, center_width)
    assert dim.w_side + dim.w_center + dim.w_side == screen_width


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
