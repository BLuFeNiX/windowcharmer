"""Unit tests for KeyboardMapper that require no real X server connection."""

from unittest.mock import MagicMock, patch

from Xlib import XK

from windowcharmer.x11.keyboard_mapper import (
    KeyboardMapper,
    KeysymPositions,
    scan_keymap_positions,
)

_SUPER_L = XK.string_to_keysym("Super_L")
_HYPER_L = XK.string_to_keysym("Hyper_L")

_CANON_SUPER_KC = 133  # canonical (lower) Super_L position
_CANON_HYPER_KC = 207  # canonical (higher) Hyper_L position


def _make_dpy(mapping: dict[int, list[int]]) -> MagicMock:
    """Construct a stub Display whose get_keyboard_mapping reads from `mapping`."""
    dpy = MagicMock()

    def _get_keyboard_mapping(kc: int, count: int) -> list[list[int]]:
        return [mapping.get(kc + i, []) for i in range(count)]

    dpy.get_keyboard_mapping.side_effect = _get_keyboard_mapping
    dpy.display.info.min_keycode = 8
    dpy.display.info.max_keycode = 255
    return dpy


def _make_mapper(mapping: dict[int, list[int]]) -> KeyboardMapper:
    """Construct a KeyboardMapper with a stub Display backed by `mapping`."""
    dpy = _make_dpy(mapping)
    with patch("windowcharmer.x11.keyboard_mapper.DisplayPool") as pool:
        pool.get_display.return_value = dpy
        return KeyboardMapper()


def _bind_live_dict(mapper: KeyboardMapper, live: dict[int, list[int]]) -> None:
    """Wire mapper._dpy so reads/writes act on `live`."""
    mapper._dpy.get_keyboard_mapping.side_effect = lambda kc, count: [
        live.get(kc + i, []) for i in range(count)
    ]
    mapper._dpy.change_keyboard_mapping.side_effect = lambda kc, keysyms: live.__setitem__(
        kc, list(keysyms[0])
    )


# ---------------------------------------------------------------------------
# scan_keymap_positions — pure function, can be tested without a mapper.
# ---------------------------------------------------------------------------


def test_scan_returns_zero_for_missing_keysyms() -> None:
    dpy = _make_dpy({_CANON_SUPER_KC: [_SUPER_L]})  # no Hyper_L anywhere
    p = scan_keymap_positions(dpy, _SUPER_L, _HYPER_L)
    assert p.super_l_kc == _CANON_SUPER_KC
    assert p.hyper_l_kc == 0
    assert p.both_present is False


def test_scan_prefers_level_zero_over_residual_at_higher_levels() -> None:
    """The collapse-bug fixture: level-0 Hyper_L at kc 133 plus residual
    Super_L at level 2 of kc 133, AND level-0 Super_L at kc 207. An "any-level"
    scan would find both keysyms at kc 133 and stop. Level-0-first must
    correctly report Super_L at 207, Hyper_L at 133.
    """
    dpy = _make_dpy(
        {
            _CANON_SUPER_KC: [_HYPER_L, 0, _SUPER_L],
            _CANON_HYPER_KC: [_SUPER_L, _HYPER_L, 0, _HYPER_L],
        }
    )
    p = scan_keymap_positions(dpy, _SUPER_L, _HYPER_L)
    assert p.super_l_kc == _CANON_HYPER_KC, "Super_L should resolve to its level-0 home (kc 207)"
    assert p.hyper_l_kc == _CANON_SUPER_KC, "Hyper_L should resolve to its level-0 home (kc 133)"


def test_scan_falls_back_to_higher_levels_when_level_zero_missing() -> None:
    """xkb US layout has Hyper_L only at level 1 of kc 207. Level-0-only would
    miss it; pass 2 must find it at any shift level.
    """
    dpy = _make_dpy(
        {
            _CANON_SUPER_KC: [_SUPER_L, 0, _SUPER_L],
            _CANON_HYPER_KC: [0, _HYPER_L, 0, _HYPER_L],
        }
    )
    p = scan_keymap_positions(dpy, _SUPER_L, _HYPER_L)
    assert p.super_l_kc == _CANON_SUPER_KC
    assert p.hyper_l_kc == _CANON_HYPER_KC


def test_scan_returns_lowest_keycode_when_keysym_appears_multiple_times() -> None:
    """If the keysym is genuinely at level 0 of multiple keycodes (rare but
    possible), pass 1 picks the lowest — matching Xlib's keysym_to_keycode
    cache build order, so callers don't see a confusing skip."""
    dpy = _make_dpy(
        {
            _CANON_SUPER_KC: [_SUPER_L],
            150: [_SUPER_L],  # duplicate Super_L at higher kc
            _CANON_HYPER_KC: [_HYPER_L],
        }
    )
    p = scan_keymap_positions(dpy, _SUPER_L, _HYPER_L)
    assert p.super_l_kc == _CANON_SUPER_KC


def test_keysym_positions_physical_helpers_are_swap_invariant() -> None:
    """The physical_*_kc properties are stable under swapping the two values."""
    canonical = KeysymPositions(super_l_kc=133, hyper_l_kc=207)
    swapped = KeysymPositions(super_l_kc=207, hyper_l_kc=133)
    assert canonical.physical_super_kc == swapped.physical_super_kc == 133
    assert canonical.physical_hyper_kc == swapped.physical_hyper_kc == 207


# ---------------------------------------------------------------------------
# KeyboardMapper init / backup
# ---------------------------------------------------------------------------


def test_init_captures_canonical_originals_for_cleanup() -> None:
    """Behavior test: cleanup() after init on a canonical keymap restores
    exactly the rows that were live at __init__.
    """
    canonical_super = [_SUPER_L]
    canonical_hyper = [_HYPER_L]
    live = {_CANON_SUPER_KC: list(canonical_super), _CANON_HYPER_KC: list(canonical_hyper)}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    # Mutate the keymap, then cleanup — should be restored exactly.
    live[_CANON_SUPER_KC] = [0xDEAD]
    live[_CANON_HYPER_KC] = [0xBEEF]
    mapper.cleanup()

    assert live[_CANON_SUPER_KC] == canonical_super
    assert live[_CANON_HYPER_KC] == canonical_hyper


def test_init_recovers_from_inverted_state() -> None:
    """Previous run crashed mid-swap: kc 133 holds Hyper_L, kc 207 holds Super_L.
    Without recovery, _capture_backup would snapshot the inverted rows and
    cleanup would re-apply them, leaving the keymap permanently inverted.
    Recovery synthesizes minimal canonical originals.
    """
    live = {_CANON_SUPER_KC: [_HYPER_L], _CANON_HYPER_KC: [_SUPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    mapper.cleanup()

    assert live[_CANON_SUPER_KC] == [_SUPER_L], "cleanup must put Super_L back at canonical Super position"
    assert live[_CANON_HYPER_KC] == [_HYPER_L], "cleanup must put Hyper_L back at canonical Hyper position"


def test_init_skips_backup_when_keysym_missing() -> None:
    """If Hyper_L is absent from the keymap, _backup stays None and cleanup is a no-op."""
    live = {_CANON_SUPER_KC: [_SUPER_L]}
    mapper = _make_mapper(live)
    assert mapper._backup is None

    # cleanup should not raise and should not write anything.
    _bind_live_dict(mapper, live)
    write_count = mapper._dpy.change_keyboard_mapping.call_count
    mapper.cleanup()
    assert mapper._dpy.change_keyboard_mapping.call_count == write_count


# ---------------------------------------------------------------------------
# apply_super_hyper_swap — idempotency, recanonicalization, missing-keysym safety
# ---------------------------------------------------------------------------


def test_apply_swap_idempotent_across_self_triggered_mapping_notify() -> None:
    """Every change_keyboard_mapping broadcasts MappingNotify, which routes back
    here via the KeyGrabber. The second call must detect the layout is already
    swapped and bail — otherwise we'd loop forever.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    mapper.apply_super_hyper_swap()
    assert live[_CANON_SUPER_KC] == [_HYPER_L]
    assert live[_CANON_HYPER_KC] == [_SUPER_L]

    writes_after_first = mapper._dpy.change_keyboard_mapping.call_count
    # Simulate the MappingNotify echo re-entering apply_super_hyper_swap.
    mapper.apply_super_hyper_swap()
    assert mapper._dpy.change_keyboard_mapping.call_count == writes_after_first, (
        "Second call wrote — would loop forever in production"
    )


def test_apply_swap_recanonicalizes_after_setxkbmap_moves_super() -> None:
    """A mid-session keymap change can move Super_L to a different keycode.
    apply_super_hyper_swap must re-derive canon from the live keymap and target
    the new positions — otherwise it would clobber whatever now lives at the
    stale canonical position.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    new_super_kc = 100
    del live[_CANON_SUPER_KC]
    live[new_super_kc] = [_SUPER_L]

    mapper.apply_super_hyper_swap()

    new_canon_super = min(new_super_kc, _CANON_HYPER_KC)
    new_canon_hyper = max(new_super_kc, _CANON_HYPER_KC)
    assert live[new_canon_super] == [_HYPER_L]
    assert live[new_canon_hyper] == [_SUPER_L]
    assert _CANON_SUPER_KC not in live, "old canon Super position should be left alone"


def test_apply_swap_skips_when_super_l_missing_from_keymap() -> None:
    """If Super_L is removed entirely (rare, but possible after a custom xkb load),
    the swap should bail rather than write at stale positions.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)
    del live[_CANON_SUPER_KC]

    writes_before = mapper._dpy.change_keyboard_mapping.call_count
    mapper.apply_super_hyper_swap()
    assert mapper._dpy.change_keyboard_mapping.call_count == writes_before


def test_apply_swap_handles_hyper_l_only_at_shift_level() -> None:
    """Real xkb US layout: Hyper_L bound only at level 1 of kc 207. Pre-fix
    the level-0-only scan made the daemon log "Hyper_L missing" and skip the
    swap on every keystroke.
    """
    live = {
        _CANON_SUPER_KC: [_SUPER_L, 0, _SUPER_L, 0],
        _CANON_HYPER_KC: [0, _HYPER_L, 0, _HYPER_L],
    }
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    mapper.apply_super_hyper_swap()

    # Level 0 swapped; level 1+ preserved.
    assert live[_CANON_SUPER_KC] == [_HYPER_L, 0, _SUPER_L, 0]
    assert live[_CANON_HYPER_KC] == [_SUPER_L, _HYPER_L, 0, _HYPER_L]


# ---------------------------------------------------------------------------
# Round-trip on the user's real xkb US layout — the regression that motivated
# the level-0-preferred scan.
# ---------------------------------------------------------------------------


def test_full_swap_roundtrip_on_user_layout_restores_canonical() -> None:
    """End-to-end: swap → MappingNotify echo → cleanup must leave the keymap
    bit-for-bit identical to the canonical starting state on a layout where
    Super_L appears at level 0 AND level 2 of kc 133. Pre-fix this corrupted
    the keymap (cleanup wrote both rows to the same kc due to canon collapse).
    """
    canonical_133 = [_SUPER_L, 0, _SUPER_L]
    canonical_207 = [0, _HYPER_L, 0, _HYPER_L]
    live = {
        _CANON_SUPER_KC: list(canonical_133),
        _CANON_HYPER_KC: list(canonical_207),
    }
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    mapper.apply_super_hyper_swap()
    mapper.apply_super_hyper_swap()  # MappingNotify echo
    mapper.cleanup()

    assert live[_CANON_SUPER_KC] == canonical_133
    assert live[_CANON_HYPER_KC] == canonical_207


# ---------------------------------------------------------------------------
# physical_super_kc — what the passthrough tracker watches.
# ---------------------------------------------------------------------------


def test_physical_super_kc_is_stable_across_swap() -> None:
    """The passthrough tracker watches the physical Super key — it must not
    move when the keysyms are exchanged.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)
    assert mapper.physical_super_kc() == _CANON_SUPER_KC

    mapper.apply_super_hyper_swap()
    assert mapper.physical_super_kc() == _CANON_SUPER_KC, (
        "physical Super position must not change when we swap the keysyms"
    )


def test_physical_super_kc_returns_zero_when_keysym_missing() -> None:
    live = {_CANON_SUPER_KC: [_SUPER_L]}  # Hyper_L missing
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)
    assert mapper.physical_super_kc() == 0


# ---------------------------------------------------------------------------
# force_canonical
# ---------------------------------------------------------------------------


def test_force_canonical_restores_super_at_lower_hyper_at_higher() -> None:
    """After --fix-keymap on a swapped session, force_canonical must put
    Super_L at the lower currently-occupied keycode and Hyper_L at the higher.
    """
    live = {_CANON_SUPER_KC: [_HYPER_L], _CANON_HYPER_KC: [_SUPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    assert mapper.force_canonical() is True
    assert live[_CANON_SUPER_KC] == [_SUPER_L]
    assert live[_CANON_HYPER_KC] == [_HYPER_L]


def test_force_canonical_returns_false_when_keysym_missing() -> None:
    live = {_CANON_SUPER_KC: [_SUPER_L]}  # Hyper_L missing
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)
    assert mapper.force_canonical() is False


# ---------------------------------------------------------------------------
# simulate_super_press
# ---------------------------------------------------------------------------


def test_simulate_super_press_targets_current_super_l_position_post_swap() -> None:
    """The bare-tap passthrough must produce a synthetic Super_L event so the
    DE's default 'show menu' binding (typically on Super_L) fires. Post-swap
    the Super_L keysym lives at the canonical Hyper position (kc 207). Faking
    at the current Hyper_L position (kc 133) instead would generate a Hyper_L
    event — which doesn't match Cinnamon's default menu binding, so the menu
    would not open. This test locks in the right post-swap target.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)
    mapper.apply_super_hyper_swap()

    with patch("windowcharmer.x11.keyboard_mapper.xtest.fake_input") as fake:
        mapper.simulate_super_press()

    keycodes_used = {call.args[2] for call in fake.call_args_list}
    assert keycodes_used == {_CANON_HYPER_KC}, (
        f"expected fake_input at kc {_CANON_HYPER_KC} (post-swap Super_L home), got {keycodes_used}"
    )


def test_simulate_super_press_targets_canonical_super_position_pre_swap() -> None:
    """Before any swap, Super_L lives at the canonical Super position.
    The simulate must fake there so a Super_L event is produced even if the
    daemon happens to call simulate before its first swap completes."""
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(live)
    _bind_live_dict(mapper, live)

    with patch("windowcharmer.x11.keyboard_mapper.xtest.fake_input") as fake:
        mapper.simulate_super_press()

    keycodes_used = {call.args[2] for call in fake.call_args_list}
    assert keycodes_used == {_CANON_SUPER_KC}
