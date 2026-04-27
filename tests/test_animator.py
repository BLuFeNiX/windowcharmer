"""Unit tests for CinnamonAnimator that require no D-Bus session."""

from unittest.mock import patch

from windowcharmer.cinnamon.animator import CinnamonAnimator


def _make_animator_with_call(call_results: list[object]) -> tuple[CinnamonAnimator, list[str]]:
    """Build an animator whose probe returns each entry from call_results in turn.

    Each entry is either a (ok, val) tuple to return or an Exception to raise.
    """
    calls: list[str] = []
    iterator = iter(call_results)

    def fake_call(script: str) -> tuple[bool, str]:
        calls.append(script)
        result = next(iterator)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]

    with patch("windowcharmer.cinnamon.animator._make_gi_caller", return_value=fake_call):
        animator = CinnamonAnimator(disabled=False)
    return animator, calls


def test_disabled_never_probes() -> None:
    animator = CinnamonAnimator(disabled=True)
    calls: list[str] = []
    animator._call = lambda s: (calls.append(s), (True, "2"))[1]  # type: ignore[assignment,return-value]

    assert animator.is_available() is False
    assert calls == []


def test_successful_probe_is_cached() -> None:
    animator, calls = _make_animator_with_call([(True, "2"), (True, "2")])
    assert animator.is_available() is True
    assert animator.is_available() is True
    assert len(calls) == 1  # second call hits cache


def test_failed_probe_reprobes_after_cooldown() -> None:
    animator, calls = _make_animator_with_call([(False, ""), (True, "2")])

    with patch("windowcharmer.cinnamon.animator.time.monotonic", return_value=100.0):
        assert animator.is_available() is False
    # Within the cooldown — still cached as unavailable.
    with patch("windowcharmer.cinnamon.animator.time.monotonic", return_value=110.0):
        assert animator.is_available() is False
    assert len(calls) == 1
    # After cooldown — re-probes and recovers.
    with patch("windowcharmer.cinnamon.animator.time.monotonic", return_value=200.0):
        assert animator.is_available() is True
    assert len(calls) == 2


def test_exception_during_probe_is_treated_as_failure() -> None:
    animator, calls = _make_animator_with_call([RuntimeError("bus gone"), (True, "2")])

    with patch("windowcharmer.cinnamon.animator.time.monotonic", return_value=0.0):
        assert animator.is_available() is False
    with patch("windowcharmer.cinnamon.animator.time.monotonic", return_value=100.0):
        assert animator.is_available() is True
    assert len(calls) == 2


def test_animate_failure_invalidates_cached_availability() -> None:
    """Regression: once is_available() cached True, the original code never re-probed.
    If Cinnamon died mid-session every tile press blocked on D-Bus until daemon
    restart. animate() must reset _available on transport failure.
    """
    animator, calls = _make_animator_with_call([
        (True, "2"),  # initial probe
        RuntimeError("bus gone"),  # animate transport error
        (True, "2"),  # re-probe succeeds
    ])

    assert animator.is_available() is True
    assert animator.animate(0x1234, 0, 0, 100, 100) is False
    # _available was reset, so the next is_available() re-probes.
    assert animator.is_available() is True
    assert len(calls) == 3


def test_animate_actor_not_found_does_not_invalidate() -> None:
    """val == "0" means the script ran but the window actor wasn't there —
    a per-window issue, not a Cinnamon-down signal. Cached availability stays.
    """
    animator, calls = _make_animator_with_call([
        (True, "2"),  # probe
        (True, "0"),  # animate: actor not found
    ])

    assert animator.is_available() is True
    assert animator.animate(0x1234, 0, 0, 100, 100) is False
    assert animator.is_available() is True  # cached, no extra probe
    assert len(calls) == 2


def test_animate_eval_rejected_invalidates_cached_availability() -> None:
    """ok=False from the bus means Cinnamon refused the eval — degraded state,
    re-probe on next call.
    """
    animator, calls = _make_animator_with_call([
        (True, "2"),  # probe
        (False, "syntax error"),  # animate: eval rejected
        (True, "2"),  # re-probe
    ])

    assert animator.is_available() is True
    assert animator.animate(0x1234, 0, 0, 100, 100) is False
    assert animator.is_available() is True
    assert len(calls) == 3
