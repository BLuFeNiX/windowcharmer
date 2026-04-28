"""Tests for EwmhClient — the EWMH/X11 wrapper used by WindowManager.

Each test patches get_property_value at the module level rather than
mocking the X server, since the property layer is what we care about.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from windowcharmer.x11.ewmh_client import ALL_DESKTOPS, EwmhClient, FrameExtents


def _make_client() -> EwmhClient:
    """Build an EwmhClient against a mocked Display."""
    with patch("windowcharmer.x11.ewmh_client.Atoms") as atom_cls:
        # Give every atom a stable distinct integer so patched get_property_value
        # callbacks can `is`-compare against it.
        atom = atom_cls.return_value
        atom.workarea = 11
        atom.active_window = 12
        atom.current_desktop = 13
        atom.gtk_frame_extents = 14
        atom.frame_extents = 15
        atom.wm_state = 16
        atom.v_max = 17
        atom.h_max = 18
        atom.fullscreen = 19
        atom.client_list_stacking = 20
        atom.client_list = 21
        atom.wm_desktop = 22

        dpy = MagicMock()
        dpy.screen.return_value.root = MagicMock()
        dpy.screen.return_value.width_in_pixels = 1920
        dpy.screen.return_value.height_in_pixels = 1080
        return EwmhClient(dpy)


@pytest.fixture()
def client() -> EwmhClient:
    return _make_client()


# --- Screen / workarea ---


def test_get_screen_size(client: EwmhClient) -> None:
    assert client.get_screen_size() == (1920, 1080)


def test_get_workarea_returns_first_four_values(client: EwmhClient) -> None:
    """_NET_WORKAREA may carry per-desktop entries appended after the first
    rect. We only consume the first 4 (current desktop's workarea)."""
    with patch(
        "windowcharmer.x11.ewmh_client.get_property_value",
        return_value=[10, 20, 1900, 1060, 99, 99, 99, 99],
    ):
        assert client.get_workarea() == (10, 20, 1900, 1060)


def test_get_workarea_returns_none_when_absent(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=None):
        assert client.get_workarea() is None


def test_get_workarea_returns_none_when_truncated(client: EwmhClient) -> None:
    """A property with fewer than 4 values is malformed; treat as absent."""
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[1, 2]):
        assert client.get_workarea() is None


# --- Active window / desktop ---


def test_get_active_window_returns_none_for_zero_id(client: EwmhClient) -> None:
    """_NET_ACTIVE_WINDOW = [0] means 'no active window'."""
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[0]):
        assert client.get_active_window() is None


def test_get_active_window_creates_resource_for_real_id(client: EwmhClient) -> None:
    sentinel = object()
    client._dpy.create_resource_object.return_value = sentinel
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[0x4200001]):
        assert client.get_active_window() is sentinel
    client._dpy.create_resource_object.assert_called_once_with("window", 0x4200001)


def test_get_active_desktop_warns_only_once_when_unset(client: EwmhClient, caplog: pytest.LogCaptureFixture) -> None:
    """Warning for non-EWMH WMs must fire exactly once per process — every
    action would otherwise re-warn and spam the journal."""
    caplog.set_level(logging.WARNING, logger="windowcharmer.x11.ewmh_client")
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=None):
        assert client.get_active_desktop() == 0
        assert client.get_active_desktop() == 0
        assert client.get_active_desktop() == 0

    matches = [r for r in caplog.records if "_NET_CURRENT_DESKTOP" in r.message]
    assert len(matches) == 1


def test_get_active_desktop_returns_value_when_set(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[3]):
        assert client.get_active_desktop() == 3


# --- list_windows ---


def test_list_windows_prefers_stacking_order(client: EwmhClient) -> None:
    def _gpv(window: object, atom: int, *_: object) -> list[int] | None:
        if atom == client._atom.client_list_stacking:
            return [11, 22, 33]
        return None

    with patch("windowcharmer.x11.ewmh_client.get_property_value", side_effect=_gpv):
        client._dpy.create_resource_object.side_effect = lambda kind, wid: f"win-{wid}"
        result = client.list_windows()

    assert result == ["win-11", "win-22", "win-33"]


def test_list_windows_falls_back_to_creation_order(client: EwmhClient) -> None:
    """Some minimal WMs only expose _NET_CLIENT_LIST."""

    def _gpv(window: object, atom: int, *_: object) -> list[int] | None:
        if atom == client._atom.client_list_stacking:
            return None
        if atom == client._atom.client_list:
            return [42]
        return None

    with patch("windowcharmer.x11.ewmh_client.get_property_value", side_effect=_gpv):
        client._dpy.create_resource_object.return_value = "fallback-win"
        assert client.list_windows() == ["fallback-win"]


def test_list_windows_returns_empty_when_both_absent(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=None):
        assert client.list_windows() == []


# --- Frame extents ---


def test_gtk_frame_extents_normal(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[2, 2, 28, 2]):
        result = client.get_gtk_frame_extents(MagicMock())
    assert result == FrameExtents(left=2, right=2, top=28, bottom=2)


def test_gtk_frame_extents_truncated_returns_none(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[1, 2, 3]):
        assert client.get_gtk_frame_extents(MagicMock()) is None


def test_gtk_frame_extents_absent_returns_none(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=None):
        assert client.get_gtk_frame_extents(MagicMock()) is None


def test_net_frame_extents_normal(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[5, 5, 30, 5]):
        result = client.get_net_frame_extents(MagicMock())
    assert result == FrameExtents(left=5, right=5, top=30, bottom=5)


# --- State predicates ---


def test_is_window_maximized_vertically_true(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[client._atom.v_max, 999]):
        assert client.is_window_maximized_vertically(MagicMock()) is True


def test_is_window_maximized_vertically_false_when_only_horizontal(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[client._atom.h_max]):
        assert client.is_window_maximized_vertically(MagicMock()) is False


def test_is_window_fullscreen_when_set(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[client._atom.fullscreen]):
        assert client.is_window_fullscreen(MagicMock()) is True


def test_is_state_set_returns_false_when_property_absent(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=None):
        assert client.is_window_maximized_vertically(MagicMock()) is False
        assert client.is_window_fullscreen(MagicMock()) is False


# --- State writes ---


def test_set_max_flags_sends_two_client_messages(client: EwmhClient) -> None:
    """One ClientMessage per axis — vert and horz — both routed to root."""
    win = MagicMock()
    with patch("windowcharmer.x11.ewmh_client.protocol.event.ClientMessage") as cm_cls:
        client.set_max_flags(win, v=1, h=0)

    assert cm_cls.call_count == 2
    # First call: vert axis
    args0 = cm_cls.call_args_list[0]
    assert args0.kwargs["client_type"] == client._atom.wm_state
    assert args0.kwargs["data"] == (32, [1, client._atom.v_max, 0, 0, 0])
    # Second call: horz axis
    args1 = cm_cls.call_args_list[1]
    assert args1.kwargs["data"] == (32, [0, client._atom.h_max, 0, 0, 0])
    # Both events are sent to the root, not the target window.
    assert client._root.send_event.call_count == 2


def test_set_fullscreen_flag_on(client: EwmhClient) -> None:
    win = MagicMock()
    with patch("windowcharmer.x11.ewmh_client.protocol.event.ClientMessage") as cm_cls:
        client.set_fullscreen_flag(win, on=True)
    assert cm_cls.call_args.kwargs["data"] == (32, [1, client._atom.fullscreen, 0, 0, 0])


def test_set_fullscreen_flag_off(client: EwmhClient) -> None:
    win = MagicMock()
    with patch("windowcharmer.x11.ewmh_client.protocol.event.ClientMessage") as cm_cls:
        client.set_fullscreen_flag(win, on=False)
    assert cm_cls.call_args.kwargs["data"] == (32, [0, client._atom.fullscreen, 0, 0, 0])


# --- Sticky-window sentinel ---


def test_all_desktops_sentinel_value() -> None:
    """Any window with this _NET_WM_DESKTOP value is sticky (every desktop)."""
    assert ALL_DESKTOPS == 0xFFFFFFFF


def test_get_window_desktop_returns_value(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=[2]):
        assert client.get_window_desktop(MagicMock()) == 2


def test_get_window_desktop_returns_none_when_unset(client: EwmhClient) -> None:
    with patch("windowcharmer.x11.ewmh_client.get_property_value", return_value=None):
        assert client.get_window_desktop(MagicMock()) is None
