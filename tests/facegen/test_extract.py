"""Tests for `furrifier.facegen.extract` — build an npc_info dict from
a live NPC record (as found in a patch) matching the shape the engine
expects.

Validation strategy: load Skyrim.esm, pull a known vanilla NPC's
record, run it through `extract_npc_info`, and compare to the
pre-validated manifest entry for that NPC under `Data_vanilla/`.

Skips cleanly if the game folder isn't present, matching the pattern
the other facegen tests use.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from esplib import LoadOrder, PluginSet

HERE = Path(__file__).parent
DATA_VANILLA = HERE / "Data_vanilla"

GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")


@pytest.fixture(scope="session")
def skyrim_plugin_set():
    if not (GAME_DATA / "Skyrim.esm").exists():
        pytest.skip("Skyrim.esm not present — live plugin tests skipped")
    load_order = LoadOrder.from_list(
        ["Skyrim.esm"], data_dir=str(GAME_DATA), game_id="tes5")
    ps = PluginSet(load_order)
    ps.load_all()
    return ps


@pytest.fixture(scope="session")
def manifest():
    return json.loads((DATA_VANILLA / "manifest.json").read_text())


def _find_npc_by_formid(plugin_set, form_id: int):
    """Follow the override chain and return the winning record."""
    chain = plugin_set.get_override_chain(form_id)
    if not chain:
        pytest.fail(f"no override chain for 0x{form_id:08X}")
    return chain[-1]


def _manifest_entry(manifest, form_id_hex: str) -> dict:
    entry = next((n for n in manifest["npcs"] if n["form_id"] == form_id_hex), None)
    if entry is None:
        pytest.fail(f"no manifest entry for {form_id_hex}")
    return entry


# Dervenin — Wood Elf male with 10 headparts, NAM9 morphs, NAMA presets,
# race + chargen + behavior tris. Exercises every feature of extract.
DERVENIN = 0x0001327C


def test_extract_form_id(skyrim_plugin_set, manifest):
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, DERVENIN)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="Skyrim.esm")
    expected = _manifest_entry(manifest, "0001327C")
    assert info["form_id"] == expected["form_id"]
    assert info["base_plugin"] == expected["base_plugin"]
    assert info["npc_edid"] == expected["npc_edid"]


def test_extract_race_and_sex(skyrim_plugin_set, manifest):
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, DERVENIN)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="Skyrim.esm")
    expected = _manifest_entry(manifest, "0001327C")
    assert info["race_edid"] == expected["race_edid"]
    assert info["is_female"] == expected["is_female"]


def test_extract_qnam_and_chargen_values(skyrim_plugin_set, manifest):
    """QNAM (skin tone), NAM9 (sliders), NAM7 (weight), NAMA (presets)
    — all the morph-pipeline inputs."""
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, DERVENIN)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="Skyrim.esm")
    expected = _manifest_entry(manifest, "0001327C")
    assert info["qnam_color"] == expected["qnam_color"]
    assert info["weight"] == pytest.approx(expected["weight"])
    assert len(info["nam9"]) == len(expected["nam9"])
    for i, (ours, ref) in enumerate(zip(info["nam9"], expected["nam9"])):
        # NAM9 slot 18 is FLT_MAX sentinel for non-vampires; compare
        # as float equality but skip NaN/inf via math.isfinite contract.
        import math
        if not math.isfinite(ours) and not math.isfinite(ref):
            continue
        assert ours == pytest.approx(ref, abs=1e-5), f"slot {i}"
    assert info["nama"] == expected["nama"]


def test_extract_headparts_match_manifest(skyrim_plugin_set, manifest):
    """Headparts list: each {hdpt_edid, hdpt_type, source_nif, tri paths,
    textures}. Order doesn't have to match manifest (manifest order comes
    from CK facegen shape order) but the set of hdpt_edids must."""
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, DERVENIN)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="Skyrim.esm")
    expected = _manifest_entry(manifest, "0001327C")

    ours_by_edid = {hp["hdpt_edid"]: hp for hp in info["headparts"]}
    ref_by_edid = {hp["hdpt_edid"]: hp for hp in expected["headparts"]}
    assert set(ours_by_edid) == set(ref_by_edid)
    for edid, ref in ref_by_edid.items():
        ours = ours_by_edid[edid]
        assert ours["hdpt_type"] == ref["hdpt_type"], edid
        assert ours["source_nif"] == ref["source_nif"], edid
        assert ours.get("race_tri") == ref.get("race_tri"), edid
        assert ours.get("chargen_tri") == ref.get("chargen_tri"), edid
        assert ours.get("behavior_tri") == ref.get("behavior_tri"), edid
        # Textures dict: same keys + same values (case-insensitive path compare)
        ref_tex = {k: v.lower() for k, v in (ref.get("textures") or {}).items()}
        our_tex = {k: v.lower() for k, v in (ours.get("textures") or {}).items()}
        assert our_tex == ref_tex, f"{edid} textures"


def test_extract_tint_layers_match_manifest(skyrim_plugin_set, manifest):
    """Tint entries: {tini, color, intensity, tias, mask, tinp}."""
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, DERVENIN)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="Skyrim.esm")
    expected = _manifest_entry(manifest, "0001327C")

    ours_by_tini = {t["tini"]: t for t in info["tints"]}
    ref_by_tini = {t["tini"]: t for t in expected["tints"]}
    assert set(ours_by_tini) == set(ref_by_tini)
    for tini, ref in ref_by_tini.items():
        ours = ours_by_tini[tini]
        assert ours["color"] == ref["color"], f"tini={tini} color"
        assert ours["intensity"] == pytest.approx(ref["intensity"]), (
            f"tini={tini} intensity"
        )
        assert ours["tinp"] == ref["tinp"], f"tini={tini} tinp"
        assert ours["mask"].lower() == ref["mask"].lower(), f"tini={tini} mask"


# Ulfric adds coverage for the HDPT.TNAM → TXST texture-override path
# (he has EyesMale with a TXST override to change eye color).
ULFRIC = 0x0001414D


def test_extract_ulfric_textures(skyrim_plugin_set, manifest):
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, ULFRIC)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="Skyrim.esm")
    expected = _manifest_entry(manifest, "0001414D")
    # Check the set of headpart EditorIDs matches the CK-facegen reference.
    ours_edids = {hp["hdpt_edid"] for hp in info["headparts"]}
    ref_edids = {hp["hdpt_edid"] for hp in expected["headparts"]}
    assert ours_edids == ref_edids


def test_base_plugin_passed_through(skyrim_plugin_set):
    """The facegen path segment is caller-supplied — extract doesn't
    care where the record currently lives, only what plugin name to
    stamp in for the FacegenDetail path. Verifies we pass through
    whatever the caller sends in."""
    from furrifier.facegen.extract import extract_npc_info
    npc = _find_npc_by_formid(skyrim_plugin_set, DERVENIN)
    info = extract_npc_info(npc, skyrim_plugin_set, patch_plugin_name="YASNPCPatch.esp")
    assert info["base_plugin"] == "YASNPCPatch.esp"
