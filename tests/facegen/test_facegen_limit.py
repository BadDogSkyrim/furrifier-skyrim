"""Tests for build_facegen_for_patch's NPC-count limit.

The limit lets users preview a scheme's output on a subset of NPCs
before committing to a full-load-order bake (slow — minutes for
thousands of NPCs). Verifies: limit caps the work, None runs
everything, and the skip is reported in the log.
"""
from __future__ import annotations

import logging
from unittest.mock import patch as mock_patch

import pytest


def _stub_patch(npc_records):
    """Minimal stand-in for an esplib Plugin — just enough shape for
    build_facegen_for_patch to iterate NPCs."""
    from unittest.mock import MagicMock
    from pathlib import Path

    patch_obj = MagicMock()
    patch_obj.file_path = Path("Fake.esp")
    patch_obj.header.masters = ["Skyrim.esm"]
    patch_obj.get_records_by_signature.return_value = npc_records
    return patch_obj


def _stub_plugin_set():
    """Minimal PluginSet stand-in for the _inject_patch_into_plugin_set
    call. Doesn't need to resolve anything — we short-circuit out of
    build_facegen_for_patch before any form-id lookups run."""
    from unittest.mock import MagicMock
    ps = MagicMock()
    ps._plugins = {}
    ps.load_order.plugins = []
    return ps


def _stub_npc(form_id: int, editor_id: str = "Stub"):
    """Fake NPC record with just enough surface for the filter + logging."""
    from unittest.mock import MagicMock
    from esplib.utils import LocalFormID
    npc = MagicMock()
    npc.form_id = LocalFormID(form_id)
    npc.editor_id = editor_id
    # No ACBS → _is_chargen_preset returns False (catch-all path)
    npc.__getitem__.side_effect = KeyError("no ACBS")
    npc.get_subrecord.return_value = None
    return npc


def test_limit_caps_extract_calls(tmp_path, caplog):
    """When limit=N is set and there are N+extra NPCs in the patch,
    only N get through to extract_npc_info."""
    from furrifier import facegen as fg_module

    npcs = [_stub_npc(0x0100_0000 + i, f"Npc{i}") for i in range(5)]
    patch_obj = _stub_patch(npcs)

    call_count = 0

    def _tracking_extract(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Raise so we don't have to stub the rest of the build pipeline.
        raise RuntimeError("skip downstream work")

    with mock_patch.object(fg_module, "extract_npc_info",
                           side_effect=_tracking_extract):
        with caplog.at_level(logging.INFO):
            fg_module.build_facegen_for_patch(
                patch_obj, plugin_set=_stub_plugin_set(),
                data_dir=tmp_path, output_dir=tmp_path,
                limit=3)

    assert call_count == 3, (
        f"limit=3 should cap extract_npc_info to 3 calls; got {call_count}"
    )
    # The cap should be announced in the log so users know it fired.
    joined = " ".join(r.message for r in caplog.records)
    assert "limit" in joined.lower() or "cap" in joined.lower()


def test_limit_none_runs_all(tmp_path):
    """limit=None (default) must process every NPC in the patch."""
    from furrifier import facegen as fg_module

    npcs = [_stub_npc(0x0100_0000 + i, f"Npc{i}") for i in range(4)]
    patch_obj = _stub_patch(npcs)

    call_count = 0

    def _tracking_extract(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("skip downstream work")

    with mock_patch.object(fg_module, "extract_npc_info",
                           side_effect=_tracking_extract):
        fg_module.build_facegen_for_patch(
            patch_obj, plugin_set=_stub_plugin_set(),
            data_dir=tmp_path, output_dir=tmp_path)

    assert call_count == 4


def test_limit_larger_than_count_is_a_noop(tmp_path):
    """limit=1000 against 3 NPCs should run all 3 (not crash, not
    under-report)."""
    from furrifier import facegen as fg_module

    npcs = [_stub_npc(0x0100_0000 + i, f"Npc{i}") for i in range(3)]
    patch_obj = _stub_patch(npcs)

    call_count = 0

    def _tracking_extract(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("skip downstream work")

    with mock_patch.object(fg_module, "extract_npc_info",
                           side_effect=_tracking_extract):
        fg_module.build_facegen_for_patch(
            patch_obj, plugin_set=_stub_plugin_set(),
            data_dir=tmp_path, output_dir=tmp_path,
            limit=1000)

    assert call_count == 3
