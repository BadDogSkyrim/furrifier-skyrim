"""Tests for the live-preview pane's non-rendering logic.

Per Hugh's rule, UIs are a hassle to test — these tests cover the
pieces that are worth asserting without a QApplication + display:

- `RequestTracker` — monotonic IDs and stale-request detection.
- `NpcEntry` — label formatting (what the dropdown shows).
- `NpcPickerWidget._lookup` — the text → entry resolution used when
  the user presses Enter after typing. Needs a QApplication but
  doesn't render anything.
"""
from __future__ import annotations

import pytest

from furrifier.preview.npc_picker import NpcEntry, NpcPickerWidget
from furrifier.preview.worker import RequestTracker


# --- pure: no QApplication needed ------------------------------------------


def test_request_tracker_assigns_monotonic_ids():
    t = RequestTracker()
    ids = [t.next_id() for _ in range(5)]
    assert ids == [1, 2, 3, 4, 5]


def test_request_tracker_recognizes_latest():
    t = RequestTracker()
    first = t.next_id()
    second = t.next_id()
    # `second` is the latest; `first` is stale.
    assert not t.is_current(first)
    assert t.is_current(second)
    # Issuing a new one makes `second` stale in turn.
    t.next_id()
    assert not t.is_current(second)


def test_npc_entry_label_has_editor_id_and_form_id():
    e = NpcEntry(form_id=0x0001_327C, editor_id="Dervenin")
    assert e.editor_id in e.label()
    assert "0001327C" in e.label()


# --- requires QApplication but no rendering --------------------------------


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_picker_lookup_matches_editor_id(qapp):
    picker = NpcPickerWidget()
    try:
        picker.set_entries([
            NpcEntry(0x0001_0001, "Balgruuf"),
            NpcEntry(0x0001_0002, "Ulfric"),
            NpcEntry(0x0001_0003, "Dervenin"),
        ])
        # Full editor id, case-insensitive
        assert picker._lookup("ulfric") is not None
        assert picker._lookup("ulfric").form_id == 0x0001_0002
        # Substring fallback
        assert picker._lookup("derv") is not None
        assert picker._lookup("derv").form_id == 0x0001_0003
        # Full label
        assert picker._lookup("Balgruuf  (00010001)") is not None
        # Empty / no match
        assert picker._lookup("") is None
        assert picker._lookup("XxxYyyZzz") is None
    finally:
        picker.deleteLater()


def test_picker_set_entries_replaces_prior_state(qapp):
    picker = NpcPickerWidget()
    try:
        picker.set_entries([NpcEntry(1, "One")])
        assert picker.count() == 1
        picker.set_entries([
            NpcEntry(2, "Two"), NpcEntry(3, "Three"),
        ])
        assert picker.count() == 2
        # Current selection should reset — user should pick fresh.
        assert picker.currentIndex() == -1
    finally:
        picker.deleteLater()
