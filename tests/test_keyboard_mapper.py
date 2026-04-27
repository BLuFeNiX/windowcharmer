"""Unit tests for KeyboardMapper that require no real X server connection."""

from unittest.mock import MagicMock, patch

from Xlib import XK

from windowcharmer.x11.keyboard_mapper import KeyboardMapper

_SUPER_L = XK.string_to_keysym("Super_L")
_HYPER_L = XK.string_to_keysym("Hyper_L")

_CANON_SUPER_KC = 133  # canonical (lower) Super_L position
_CANON_HYPER_KC = 207  # canonical (higher) Hyper_L position


def _make_mapper(super_kc: int, hyper_kc: int, mapping: dict[int, list[int]]) -> KeyboardMapper:
    """Construct a KeyboardMapper with a stub Display.

    super_kc/hyper_kc are what keysym_to_keycode returns at __init__ —
    in the inverted state these are flipped relative to the canonical layout.
    `mapping` maps keycode → list of keysyms returned by get_keyboard_mapping.
    """
    dpy = MagicMock()

    def _keysym_to_keycode(keysym: int) -> int:
        if keysym == _SUPER_L:
            return super_kc
        if keysym == _HYPER_L:
            return hyper_kc
        return 0

    def _get_keyboard_mapping(kc: int, count: int) -> list[list[int]]:
        return [mapping.get(kc + i, []) for i in range(count)]

    dpy.keysym_to_keycode.side_effect = _keysym_to_keycode
    dpy.get_keyboard_mapping.side_effect = _get_keyboard_mapping

    with patch("windowcharmer.x11.keyboard_mapper.DisplayPool") as pool:
        pool.get_display.return_value = dpy
        return KeyboardMapper()


def test_backup_captures_canonical_state() -> None:
    """At startup with canonical mapping, originals are the live mappings."""
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=_CANON_HYPER_KC,
        mapping={_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]},
    )
    assert mapper.super_l_orig == [[_SUPER_L]]
    assert mapper.hyper_l_orig == [[_HYPER_L]]
    assert mapper.super_l_keycode == _CANON_SUPER_KC
    assert mapper.hyper_l_keycode == _CANON_HYPER_KC


def test_backup_recovers_from_inverted_state() -> None:
    """A previous run crashed mid-swap: the lower keycode now holds Hyper_L
    and the higher one holds Super_L. keysym_to_keycode reflects the inverted
    state, so super_l_keycode at __init__ is the canonical Hyper position.
    Backup must detect this and stage canonical restores.
    """
    mapper = _make_mapper(
        super_kc=_CANON_HYPER_KC,
        hyper_kc=_CANON_SUPER_KC,
        mapping={_CANON_SUPER_KC: [_HYPER_L], _CANON_HYPER_KC: [_SUPER_L]},
    )
    assert mapper.super_l_orig == [[_SUPER_L]]
    assert mapper.hyper_l_orig == [[_HYPER_L]]
    # Cached keycodes must be re-canonicalized (low for Super, high for Hyper)
    # so apply_super_hyper_swap() and cleanup() target the right positions.
    assert mapper.super_l_keycode == _CANON_SUPER_KC
    assert mapper.hyper_l_keycode == _CANON_HYPER_KC


def test_backup_skips_when_keysym_missing() -> None:
    """If Hyper_L isn't mapped, leave originals as None — cleanup logs and bails."""
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=0,
        mapping={_CANON_SUPER_KC: [_SUPER_L]},
    )
    assert mapper.super_l_orig is None
    assert mapper.hyper_l_orig is None


def test_apply_swap_idempotent_across_self_triggered_mapping_notify() -> None:
    """Regression: every change_keyboard_mapping call causes the X server to
    broadcast MappingNotify, which the KeyGrabber routes back to
    apply_super_hyper_swap(). The second call MUST detect the layout is
    already swapped and bail — otherwise the daemon swaps, MappingNotify
    fires, the daemon swaps again, etc., DoSing the X server until killed.

    Reproduce: have get_keyboard_mapping return live state that updates as
    change_keyboard_mapping is called, then call apply_super_hyper_swap
    twice. The second call must not write anything more.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=_CANON_HYPER_KC,
        mapping=live,  # backup reads canonical state
    )

    # Tie subsequent get/change calls to the live dict so apply_super_hyper_swap
    # observes its own writes — exactly what happens when the X server replays
    # the swap back via MappingNotify.
    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        live[kc] = [keysyms[0][0]]

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    mapper.apply_super_hyper_swap()
    assert live[_CANON_SUPER_KC] == [_HYPER_L]
    assert live[_CANON_HYPER_KC] == [_SUPER_L]

    writes_after_first = mapper._dpy.change_keyboard_mapping.call_count

    # Simulate the MappingNotify echo from the swap above re-entering
    # apply_super_hyper_swap via the KeyGrabber callback.
    mapper.apply_super_hyper_swap()

    assert mapper._dpy.change_keyboard_mapping.call_count == writes_after_first, (
        "Second call wrote — would loop forever in production"
    )
