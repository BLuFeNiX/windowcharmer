import pytest

from windowcharmer.config.dimensions import ScreenDimensions


@pytest.mark.parametrize(
    "wa_w, wa_y, wa_h, center_width",
    [
        (2560, 0, 1440, 853),  # 2560x1440, ~1/3 center (853*3=2559; 1px gap from //2)
        (3840, 0, 1600, 1536),  # 3840x1600, 40% center
        (5120, 40, 1400, 2048),  # 5120x1440 with 40px panel, 40% center
        (2560, 0, 1440, 0),  # two-column mode (no center)
    ],
)
def test_column_adjacency(wa_w: int, wa_y: int, wa_h: int, center_width: int) -> None:
    dim = ScreenDimensions(0, wa_y, wa_w, wa_h, center_width)
    assert dim.x_left + dim.w_side == dim.x_center
    assert dim.x_center + dim.w_center == dim.x_right


@pytest.mark.parametrize(
    "wa_w, center_width",
    [
        (1921, 768),  # 3-column odd width
        (1920, 768),  # 3-column even width
        (1921, 0),  # 2-column odd width — leftover sits in w_center (unused)
    ],
)
def test_columns_cover_full_width(wa_w: int, center_width: int) -> None:
    dim = ScreenDimensions(0, 0, wa_w, 1080, center_width)
    assert dim.w_side + dim.w_center + dim.w_side == wa_w


@pytest.mark.parametrize(
    "wa_w, wa_y, wa_h, center_width",
    [
        (2560, 0, 1440, 853),
        (5120, 40, 1400, 2048),
    ],
)
def test_vertical_split(wa_w: int, wa_y: int, wa_h: int, center_width: int) -> None:
    dim = ScreenDimensions(0, wa_y, wa_w, wa_h, center_width)
    assert dim.y_bottom - dim.y_top == dim.h_half
    assert dim.h_half * 2 == dim.h_full


def test_wa_y_offset() -> None:
    dim = ScreenDimensions(0, 40, 2560, 1400, 0)
    assert dim.y_top == 40
    assert dim.y_bottom == 40 + 700
    assert dim.h_full == 1400


def test_left_panel_shifts_layout() -> None:
    """A left panel of 60px (wa_x=60, wa_w=screen_width-60) must offset every
    horizontal column by 60 — left edge at the panel boundary, not at x=0.
    """
    wa_x, wa_w = 60, 1860  # 1920-wide screen, 60px left panel
    dim = ScreenDimensions(wa_x, 0, wa_w, 1080, 768)
    # Left column starts at the panel boundary, not at the screen edge.
    assert dim.x_left == 60
    # All three columns still tile to exactly wa_w, with the right edge at
    # the workarea's right edge (60 + 1860 = 1920).
    assert dim.x_left + dim.w_side == dim.x_center
    assert dim.x_center + dim.w_center == dim.x_right
    assert dim.x_right + dim.w_side == wa_x + wa_w


def test_right_panel_shifts_right_edge() -> None:
    """A right panel of 60px (wa_x=0, wa_w=screen_width-60) must pull the right
    column inward so it stops at the workarea boundary, not the screen edge.
    """
    wa_x, wa_w = 0, 1860
    dim = ScreenDimensions(wa_x, 0, wa_w, 1080, 768)
    assert dim.x_left == 0
    assert dim.x_right + dim.w_side == 1860
