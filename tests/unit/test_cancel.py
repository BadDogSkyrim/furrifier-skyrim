"""Cooperative-cancel primitives wired into run_furrification.

Verifies the in-process cancel mechanism the GUI's Cancel button
relies on. The pipeline checks `cancel_event` at phase boundaries
and per-NPC checkpoints; this test only covers the primitives —
end-to-end cancellation through `run_furrification` would need a
real plugin set, so we leave that to manual GUI testing.
"""
from __future__ import annotations

import threading

import pytest

from furrifier.main import CancelledError, _check_cancel


class TestCheckCancel:
    def test_none_event_is_noop(self):
        """CLI never builds a cancel_event, so None must be a no-op."""
        _check_cancel(None)

    def test_unset_event_is_noop(self):
        e = threading.Event()
        _check_cancel(e)

    def test_set_event_raises(self):
        e = threading.Event()
        e.set()
        with pytest.raises(CancelledError):
            _check_cancel(e)

    def test_cancelled_error_is_exception_subclass(self):
        """The FaceGen per-NPC try/except catches Exception; checks
        run BEFORE the try/except so cancels propagate instead of
        being logged as per-NPC failures. Documenting the contract
        (CancelledError IS-A Exception) so future refactors don't
        reorder the check below the try/except."""
        assert issubclass(CancelledError, Exception)
