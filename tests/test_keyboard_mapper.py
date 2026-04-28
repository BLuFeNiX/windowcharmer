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
    # _refresh_keycodes_locked iterates over [min_keycode, max_keycode] —
    # set realistic X server bounds so the mapping scan returns ints.
    dpy.display.info.min_keycode = 8
    dpy.display.info.max_keycode = 255

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


def test_apply_swap_recanonicalizes_after_setxkbmap_moves_super() -> None:
    """A mid-session keymap change (setxkbmap or any other Xkb tool) can move
    Super_L to a different keycode. apply_super_hyper_swap must re-derive
    canon from the live keymap and target the new positions — otherwise it
    would clobber whatever now lives at the stale canonical position.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=_CANON_HYPER_KC,
        mapping=live,
    )

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        live[kc] = [keysyms[0][0]]

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    # Simulate setxkbmap moving Super_L from _CANON_SUPER_KC=133 to a new keycode 100.
    new_super_kc = 100
    del live[_CANON_SUPER_KC]
    live[new_super_kc] = [_SUPER_L]
    # Hyper_L stays at _CANON_HYPER_KC=207.

    mapper.apply_super_hyper_swap()

    # Canon should re-derive to (min(100, 207), max(...)) = (100, 207).
    new_canon_super = min(new_super_kc, _CANON_HYPER_KC)
    new_canon_hyper = max(new_super_kc, _CANON_HYPER_KC)
    assert live[new_canon_super] == [_HYPER_L], "swap should write Hyper_L at new canon Super position"
    assert live[new_canon_hyper] == [_SUPER_L], "swap should write Super_L at new canon Hyper position"
    # Stale canonical position must not be touched.
    assert _CANON_SUPER_KC not in live, "old canon Super position should be left alone"


def test_apply_swap_skips_when_super_l_missing_from_keymap() -> None:
    """If Super_L is removed from the keymap entirely (rare, but possible after
    a custom Xkb load), the swap should bail rather than write at stale positions.
    """
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=_CANON_HYPER_KC,
        mapping=live,
    )

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        live[kc] = [keysyms[0][0]]

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    # Remove Super_L entirely from the live keymap.
    del live[_CANON_SUPER_KC]

    writes_before = mapper._dpy.change_keyboard_mapping.call_count
    mapper.apply_super_hyper_swap()
    assert mapper._dpy.change_keyboard_mapping.call_count == writes_before


def test_force_canonical_restores_at_live_positions() -> None:
    """After --fix-keymap on a daemon-swapped session, force_canonical must
    write Super_L at the lower currently-occupied keycode and Hyper_L at the
    higher one — bypassing the keysym_to_keycode cache (which is stale on the
    mapper's Display).
    """
    # Post-swap state: keycode 133 holds Hyper_L, keycode 207 holds Super_L.
    live = {_CANON_SUPER_KC: [_HYPER_L], _CANON_HYPER_KC: [_SUPER_L]}
    mapper = _make_mapper(
        super_kc=_CANON_HYPER_KC,  # keysym_to_keycode would say Super_L lives at 207
        hyper_kc=_CANON_SUPER_KC,  # and Hyper_L at 133
        mapping=live,
    )

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        live[kc] = [keysyms[0][0]]

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    assert mapper.force_canonical() is True
    # Lower keycode gets Super_L, higher gets Hyper_L.
    assert live[_CANON_SUPER_KC] == [_SUPER_L]
    assert live[_CANON_HYPER_KC] == [_HYPER_L]


def test_refresh_finds_keysym_at_non_zero_shift_level() -> None:
    """Hyper_L is commonly bound only at level 1 (shifted) on real keymaps,
    e.g. `keycode 207 = NoSymbol Hyper_L NoSymbol Hyper_L`. The scan must
    find it there — checking only level 0 caused the daemon to log
    "Hyper_L missing" and skip the swap on every keystroke.
    """
    # Hyper_L at indices 1 and 3, never at 0.
    live = {
        _CANON_SUPER_KC: [_SUPER_L, 0, _SUPER_L, 0],
        _CANON_HYPER_KC: [0, _HYPER_L, 0, _HYPER_L],
    }
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=_CANON_HYPER_KC,
        mapping=live,
    )

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        live[kc] = [keysyms[0][0]]

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    mapper.apply_super_hyper_swap()

    # Swap should have happened — keysyms now at index 0 of canon positions.
    assert live[_CANON_SUPER_KC] == [_HYPER_L]
    assert live[_CANON_HYPER_KC] == [_SUPER_L]


def test_force_canonical_returns_false_when_keysym_missing() -> None:
    """If Super_L or Hyper_L isn't in the keymap, there's nothing to canonicalize."""
    live = {_CANON_SUPER_KC: [_SUPER_L]}  # Hyper_L missing
    mapper = _make_mapper(
        super_kc=_CANON_SUPER_KC,
        hyper_kc=0,
        mapping=live,
    )
    mapper._dpy.get_keyboard_mapping.side_effect = lambda kc, count: [live.get(kc + i, []) for i in range(count)]
    assert mapper.force_canonical() is False


def test_refresh_prefers_level_zero_over_residual_higher_levels() -> None:
    """Regression: post-swap, _change_keyboard_mapping preserves levels 1+, so
    canon-Super's keycode (where we just wrote Hyper_L at level 0) still has
    the original Super_L sitting at level 2 (xkb US: keycode 133 = Super_L
    NoSymbol Super_L). An "any-level" scan would see Super_L at kc 133 AND
    kc 207, pick 133 (the lower), and collapse both keycodes onto 133 —
    causing canon_super_kc == canon_hyper_kc, idempotency to misfire, and
    cleanup to write both originals to the same keycode (destroying the
    keymap such that the physical Super key stops producing any keysym).

    Two-pass scan must prefer level 0: Super_L lives where it appears at
    level 0 (kc 207 here), even if it also appears at higher levels of
    other keycodes.
    """
    # Post-swap state on a real US xkb layout: kc 133's level 2 still holds
    # the original Super_L (preserved by d40cd9e), kc 207's level 1 still
    # holds the original Hyper_L. Both keycodes carry both keysyms.
    mapping = {
        _CANON_SUPER_KC: [_HYPER_L, 0, _SUPER_L],
        _CANON_HYPER_KC: [_SUPER_L, _HYPER_L, 0, _HYPER_L],
    }
    mapper = _make_mapper(super_kc=_CANON_SUPER_KC, hyper_kc=_CANON_HYPER_KC, mapping=mapping)

    mapper._refresh_keycodes_locked()

    # Super_L's level-0 home is kc 207, Hyper_L's level-0 home is kc 133.
    assert mapper.super_l_keycode == _CANON_HYPER_KC, (
        f"Super_L should be tracked at its level-0 keycode (207), got {mapper.super_l_keycode}"
    )
    assert mapper.hyper_l_keycode == _CANON_SUPER_KC, (
        f"Hyper_L should be tracked at its level-0 keycode (133), got {mapper.hyper_l_keycode}"
    )
    # Canon positions stay stable across the swap (lower=Super, higher=Hyper).
    assert mapper._canon_super_kc == _CANON_SUPER_KC
    assert mapper._canon_hyper_kc == _CANON_HYPER_KC


def test_full_swap_roundtrip_on_user_layout_restores_canonical() -> None:
    """End-to-end regression for the user's actual xkb US layout, where
    Super_L is at levels 0 and 2 of kc 133 and Hyper_L is at level 1 of
    kc 207. The combination of two-pass refresh + level-preserving writes
    must round-trip cleanly: swap → echo → cleanup leaves the keymap
    bit-for-bit identical to the canonical starting state.

    Pre-fix this would corrupt: cleanup wrote both originals to kc 133
    (because canon_super_kc collapsed onto canon_hyper_kc), leaving the
    physical Super key (kc 133) with NoSymbol at level 0.
    """
    canonical_133 = [_SUPER_L, 0, _SUPER_L]
    canonical_207 = [0, _HYPER_L, 0, _HYPER_L]
    live = {
        _CANON_SUPER_KC: list(canonical_133),
        _CANON_HYPER_KC: list(canonical_207),
    }
    mapper = _make_mapper(super_kc=_CANON_SUPER_KC, hyper_kc=_CANON_HYPER_KC, mapping=live)

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        # Match the real change_keyboard_mapping(kc, [row]) signature.
        live[kc] = list(keysyms[0])

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    # Simulate the daemon lifecycle: backup happened in _make_mapper's __init__,
    # so super_l_orig/hyper_l_orig are already captured. Swap, then echo, then
    # cleanup.
    mapper.apply_super_hyper_swap()
    # Echo: MappingNotify routes back through apply_super_hyper_swap.
    mapper.apply_super_hyper_swap()
    mapper.cleanup()

    assert live[_CANON_SUPER_KC] == canonical_133, (
        f"kc 133 not restored; got {live[_CANON_SUPER_KC]} expected {canonical_133}"
    )
    assert live[_CANON_HYPER_KC] == canonical_207, (
        f"kc 207 not restored; got {live[_CANON_HYPER_KC]} expected {canonical_207}"
    )


def test_apply_swap_does_not_collapse_canon_on_user_layout() -> None:
    """Regression for the canon-collapse bug: when Super_L exists at
    level 0 of canonical-Super (kc 133) AND at level 2 of the same kc,
    the swap's MappingNotify echo must not cause canon_super_kc and
    canon_hyper_kc to collapse onto the same value.
    """
    live = {
        _CANON_SUPER_KC: [_SUPER_L, 0, _SUPER_L],
        _CANON_HYPER_KC: [0, _HYPER_L, 0, _HYPER_L],
    }
    mapper = _make_mapper(super_kc=_CANON_SUPER_KC, hyper_kc=_CANON_HYPER_KC, mapping=live)

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    def _live_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        live[kc] = list(keysyms[0])

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _live_write

    mapper.apply_super_hyper_swap()
    # Echo.
    mapper.apply_super_hyper_swap()

    assert mapper._canon_super_kc != mapper._canon_hyper_kc, (
        f"canon collapsed onto kc {mapper._canon_super_kc} — cleanup will destroy the keymap"
    )
    assert mapper._canon_super_kc == _CANON_SUPER_KC
    assert mapper._canon_hyper_kc == _CANON_HYPER_KC


def test_refresh_keycodes_returns_physical_super_kc_after_swap() -> None:
    """The passthrough tracker must watch the *physical* Super key position,
    not where the Super_L keysym moved to after the swap. refresh_keycodes
    returns canon_super_kc (the lower of the pair) so the tracker sees a
    stable physical position regardless of swap state.
    """
    # Canonical state.
    live = {_CANON_SUPER_KC: [_SUPER_L], _CANON_HYPER_KC: [_HYPER_L]}
    mapper = _make_mapper(super_kc=_CANON_SUPER_KC, hyper_kc=_CANON_HYPER_KC, mapping=live)
    assert mapper.refresh_keycodes() == _CANON_SUPER_KC

    # Post-swap state: Super_L now at canonical-Hyper position.
    live = {_CANON_SUPER_KC: [_HYPER_L], _CANON_HYPER_KC: [_SUPER_L]}
    mapper._dpy.get_keyboard_mapping.side_effect = lambda kc, count: [live.get(kc + i, []) for i in range(count)]
    assert mapper.refresh_keycodes() == _CANON_SUPER_KC, (
        "refresh_keycodes must return the physical Super position (kc 133), "
        "not where the Super_L keysym now lives (kc 207)"
    )


def test_swap_preserves_higher_shift_levels() -> None:
    """Custom xkb layouts may bind Super_L/Hyper_L at multiple shift levels
    (e.g. `keycode 207 = NoSymbol Hyper_L NoSymbol Hyper_L`). The swap must
    not truncate the row to a 1-keysym write — that would silently strip
    levels 1+ from the user's keymap, and --fix-keymap (which uses the same
    primitive) would make the loss permanent on non-default layouts.
    """
    live = {
        _CANON_SUPER_KC: [_SUPER_L, _SUPER_L, _SUPER_L, _SUPER_L],
        _CANON_HYPER_KC: [_HYPER_L, _HYPER_L, _HYPER_L, _HYPER_L],
    }
    mapper = _make_mapper(super_kc=_CANON_SUPER_KC, hyper_kc=_CANON_HYPER_KC, mapping=live)

    def _live_get(kc: int, count: int) -> list[list[int]]:
        return [live.get(kc + i, []) for i in range(count)]

    written: list[tuple[int, list[int]]] = []

    def _record_write(kc: int, keysyms: list[tuple[int, ...]]) -> None:
        written.append((kc, list(keysyms[0])))

    mapper._dpy.get_keyboard_mapping.side_effect = _live_get
    mapper._dpy.change_keyboard_mapping.side_effect = _record_write

    mapper.apply_super_hyper_swap()

    # Two writes: one at canon-Super (now Hyper_L at level 0), one at canon-Hyper.
    # Levels 1+ should retain their original keysyms — neither truncated nor remapped.
    by_kc = {kc: row for kc, row in written}
    assert by_kc[_CANON_SUPER_KC] == [_HYPER_L, _SUPER_L, _SUPER_L, _SUPER_L]
    assert by_kc[_CANON_HYPER_KC] == [_SUPER_L, _HYPER_L, _HYPER_L, _HYPER_L]
