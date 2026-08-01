"""Preserved NPCs still need faces built.

--preserve-existing keeps an earlier patch's per-NPC choices, so those
NPCs get no record in our patch. FaceGen iterates the patch, so they
used to fall out entirely: choosing "preserve" silently meant "no face"
for every NPC it applied to.

They now ride along via `extra_npcs`.
"""

from types import SimpleNamespace

import pytest

from furrifier.facegen import build_facegen_for_patch


class _FormID(int):
    pass


class _Rec:
    def __init__(self, obj_id, edid):
        self.form_id = _FormID(obj_id)
        self.editor_id = edid
        self.signature = "NPC_"

    def __getitem__(self, key):
        raise KeyError(key)

    def get_subrecord(self, sig):
        return None


class _Patch:
    def __init__(self, records):
        self._records = records
        self.file_path = SimpleNamespace(name="YASNPCPatch.esp")
        self.header = SimpleNamespace(masters=["Skyrim.esm"])

    def get_records_by_signature(self, sig):
        return list(self._records) if sig == "NPC_" else []


@pytest.fixture
def captured(monkeypatch):
    """Capture the work list build_facegen_for_patch settles on, without
    running any real baking."""
    seen = {}

    def fake_inject(plugin_set, patch):
        pass

    monkeypatch.setattr("furrifier.facegen._inject_patch_into_plugin_set",
                        fake_inject)
    monkeypatch.setattr("furrifier.facegen._is_chargen_preset",
                        lambda npc: False)

    def fake_extract(npc, plugin_set, patch_name):
        seen.setdefault("npcs", []).append(npc.editor_id)
        raise RuntimeError("stop here — the work list is what we're testing")

    monkeypatch.setattr("furrifier.facegen.extract_npc_info", fake_extract,
                        raising=False)
    return seen


def _run(patch, extra, tmp_path):
    try:
        build_facegen_for_patch(
            patch, plugin_set=SimpleNamespace(), data_dir=tmp_path,
            output_dir=tmp_path, workers=1, extra_npcs=extra)
    except Exception:
        pass


def test_preserved_npcs_are_included(captured, tmp_path, caplog):
    patch = _Patch([_Rec(0x001000, "InPatch")])
    extra = [_Rec(0x002000, "PreservedElsewhere")]

    with caplog.at_level("INFO"):
        _run(patch, extra, tmp_path)

    assert "including 1 preserved NPC" in caplog.text


def test_patch_wins_when_both_have_the_npc(captured, tmp_path, caplog):
    """An NPC the patch overrides must not be baked twice — the patch's
    version is the one that ships."""
    shared = _Rec(0x001000, "Shared")
    patch = _Patch([shared])
    extra = [_Rec(0x001000, "SharedStaleCopy")]

    with caplog.at_level("INFO"):
        _run(patch, extra, tmp_path)

    assert "preserved NPC" not in caplog.text


def test_no_extras_is_unchanged(captured, tmp_path, caplog):
    patch = _Patch([_Rec(0x001000, "InPatch")])

    with caplog.at_level("INFO"):
        _run(patch, None, tmp_path)

    assert "preserved NPC" not in caplog.text
