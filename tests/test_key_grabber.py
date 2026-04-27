"""Unit tests for KeyGrabber that require no real X server connection."""

from unittest.mock import MagicMock, patch

import pytest
from Xlib import X
from Xlib.error import BadAccess

from windowcharmer.input.key_grabber import KeyGrabber, KeyGrabberError


def _make_bad_access() -> BadAccess:
    """Build a BadAccess whose __str__ won't blow up.

    BadAccess.__init__ wants a real display object; bypass it and inject a
    minimal _data dict so logging the error (which calls __str__) works.
    """
    err = BadAccess.__new__(BadAccess)
    err._data = {
        "code": 0,
        "resource_id": 0,
        "sequence_number": 0,
        "major_opcode": 0,
        "minor_opcode": 0,
    }
    return err


def _make_grabber(events: list[object] | None = None) -> tuple[KeyGrabber, MagicMock]:
    """Build a KeyGrabber whose Display.next_event yields each entry in turn.

    Each entry may be a fake event or an exception to raise. pending_events()
    is wired up to report the remaining count so the new select-based loop
    drains everything before parking.
    """
    dpy = MagicMock()
    dpy.keysym_to_keycode.return_value = 42
    dpy.fileno.return_value = 100  # arbitrary; never read by select in tests

    if events is not None:
        iterator = iter(events)
        remaining = [len(events)]

        def _pending() -> int:
            return remaining[0]

        def _next() -> object:
            item = next(iterator)
            remaining[0] = max(0, remaining[0] - 1)
            if isinstance(item, Exception):
                raise item
            return item

        dpy.pending_events.side_effect = _pending
        dpy.next_event.side_effect = _next
    else:
        dpy.pending_events.return_value = 0

    grabber = KeyGrabber(dpy, {"Up": MagicMock()})
    return grabber, dpy


def test_stop_ends_loop_without_raising() -> None:
    grabber, _ = _make_grabber()
    grabber._stopped = True  # ensure the loop sees the flag immediately
    grabber.start()  # must return cleanly, not raise


def test_stop_after_one_event_returns_cleanly() -> None:
    """do_action(EXIT) calls grabber.stop() inside an event callback; the loop
    must finish processing that event and then exit on the next iteration.
    """
    keypress = MagicMock(type=X.KeyPress, detail=42)
    grabber, _ = _make_grabber([keypress])

    callback = MagicMock(side_effect=grabber.stop)
    grabber.key_combinations = {"Up": callback}

    grabber.start()
    callback.assert_called_once()


def test_persistent_bad_access_raises_typed_error() -> None:
    """BadAccess on both attempts must surface as KeyGrabberError so main can
    distinguish a failure from a clean stop and propagate a non-zero exit code.
    """
    grabber, _ = _make_grabber([_make_bad_access(), _make_bad_access()])

    with pytest.raises(KeyGrabberError):
        grabber.start()


def test_unexpected_exception_wrapped_as_typed_error() -> None:
    grabber, _ = _make_grabber([RuntimeError("display gone")])

    with pytest.raises(KeyGrabberError, match="display gone"):
        grabber.start()


def test_stop_during_select_wakes_loop_promptly() -> None:
    """Regression: SIGTERM (or any caller) invoking stop() while the loop is
    parked in select() must wake it without waiting for an X event. Simulate
    the wakeup by having select() observe stop() being called and return the
    wake fd as ready.
    """
    grabber, _ = _make_grabber()

    def _fake_select(rlist, wlist, xlist, timeout=None):  # type: ignore[no-untyped-def]
        # Caller (e.g. SIGTERM handler) invokes stop() — this writes a byte to
        # the wake pipe and sets _stopped. Return the wake fd as ready, just
        # like the kernel would.
        grabber.stop()
        return [grabber._wake_r], [], []

    with patch("windowcharmer.input.key_grabber.select.select", side_effect=_fake_select):
        grabber.start()  # must return cleanly, not hang


def test_bad_access_retried_once_then_succeeds() -> None:
    """One BadAccess followed by a clean stop completes without raising."""
    grabber, _ = _make_grabber()

    # First attempt raises BadAccess (via patching _run_loop directly to avoid
    # over-mocking next_event for the retry path).
    call_count = 0

    def _maybe_raise() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _make_bad_access()
        grabber._stopped = True

    grabber._run_loop = _maybe_raise  # type: ignore[method-assign]
    grabber.start()  # must not raise

    assert call_count == 2
