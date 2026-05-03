"""Tests for build_facegen_for_patch's single-NPC filter.

The `only_npc` parameter (driven by the `--only` CLI flag) restricts
the FaceGen bake to a single NPC matched by EditorID (case-insensitive)
or hexadecimal form-id object index. Used for visual debugging — Hugh
exports one NPC's nif/dds and diffs it against a CK-baked reference.
"""
from __future__ import annotations

from unittest.mock import patch as mock_patch

from tests.facegen.test_facegen_limit import _stub_npc, _stub_patch, _stub_plugin_set


def _run_with_filter(tmp_path, npcs, only_npc):
    """Drive build_facegen_for_patch with the given NPC list + filter,
    short-circuiting extract_npc_info to record which NPCs got through."""
    from furrifier import facegen as fg_module

    seen: list[str] = []

    def _tracking_extract(npc, *args, **kwargs):
        seen.append(npc.editor_id)
        raise RuntimeError("skip downstream work")

    with mock_patch.object(fg_module, "extract_npc_info",
                           side_effect=_tracking_extract):
        fg_module.build_facegen_for_patch(
            _stub_patch(npcs), plugin_set=_stub_plugin_set(),
            data_dir=tmp_path, output_dir=tmp_path,
            only_npc=only_npc)
    return seen


def test_only_npc_filters_by_edid_case_insensitive(tmp_path):
    npcs = [
        _stub_npc(0x0001C193, "UraggroShub"),
        _stub_npc(0x00013BBC, "Balgruuf"),
        _stub_npc(0x00013BBA, "Hadvar"),
    ]
    seen = _run_with_filter(tmp_path, npcs, only_npc="uraggroshub")
    assert seen == ["UraggroShub"]


def test_only_npc_filters_by_form_id_hex(tmp_path):
    npcs = [
        _stub_npc(0x0001C193, "UraggroShub"),
        _stub_npc(0x00013BBC, "Balgruuf"),
    ]
    # Hex form-id, with and without 0x prefix, full 8-digit or 6-digit object
    # index — all should resolve to the same NPC.
    for ident in ("0001C193", "0x0001C193", "1C193", "0x1c193"):
        seen = _run_with_filter(tmp_path, npcs, only_npc=ident)
        assert seen == ["UraggroShub"], f"identifier {ident!r}: got {seen}"


def test_only_npc_none_runs_all(tmp_path):
    npcs = [
        _stub_npc(0x0001C193, "UraggroShub"),
        _stub_npc(0x00013BBC, "Balgruuf"),
    ]
    seen = _run_with_filter(tmp_path, npcs, only_npc=None)
    assert set(seen) == {"UraggroShub", "Balgruuf"}


def test_only_npc_no_match_bakes_nothing(tmp_path):
    npcs = [_stub_npc(0x0001C193, "UraggroShub")]
    seen = _run_with_filter(tmp_path, npcs, only_npc="NobodySuchNPC")
    assert seen == []
