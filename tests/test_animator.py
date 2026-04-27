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
