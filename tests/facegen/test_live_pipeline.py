"""End-to-end integration test: drive the full live pipeline
(plugin_set → extract → resolver → build) without the manifest
indirection. Produces a facegen nif + DDS from a Skyrim.esm NPC using
the same code path the furrifier main loop uses.

Validates parity with the manifest-driven path we already trust — if
both produce equivalent output, the live wiring is correct.

Skips cleanly without Skyrim.esm or the extracted vanilla assets.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from esplib import LoadOrder, PluginSet


GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")
VANILLA_ASSETS = Path(r"C:\Modding\SkyrimSEAssets\00 Vanilla Assets")

HERE = Path(__file__).parent
DATA_VANILLA = HERE / "Data_vanilla"
REF_FACEGEOM = DATA_VANILLA / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"
REF_FACETINT = DATA_VANILLA / "textures/actors/character/FaceGenData/FaceTint/Skyrim.esm"

OUT_LIVE = HERE / "out_live"


DERVENIN = 0x0001327C


def _ensure_pynifly():
    p = r"C:\Modding\PyNifly\io_scene_nifly"
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="module")
def live_output(tmp_path_factory):
    """Run the live pipeline against Dervenin from Skyrim.esm against
    Hugh's vanilla-assets snapshot (loose files). Produces both the
    per-NPC nif and DDS under a tmp dir."""
    if not (GAME_DATA / "Skyrim.esm").exists():
        pytest.skip("Skyrim.esm not available")
    if not VANILLA_ASSETS.is_dir():
        pytest.skip("vanilla assets snapshot not available")

    _ensure_pynifly()
    from furrifier.facegen import (
        AssetResolver, build_facegen_nif, build_facetint_dds,
        extract_npc_info,
    )

    load_order = LoadOrder.from_list(
        ["Skyrim.esm"], data_dir=str(GAME_DATA), game_id="tes5")
    ps = PluginSet(load_order)
    ps.load_all()

    chain = ps.get_override_chain(DERVENIN)
    npc = chain[-1]

    # patch_plugin_name = "Skyrim.esm" so the FacegenDetail path stamped
    # into the nif matches the manifest fixture's convention.
    info = extract_npc_info(npc, ps, patch_plugin_name="Skyrim.esm")

    out_dir = tmp_path_factory.mktemp("live_facegen")
    form_id = info["form_id"]
    nif_path = out_dir / f"{form_id}.nif"
    # Use the vanilla-assets snapshot as the data_dir so source headpart
    # nifs / tri files / tint masks all resolve as loose files.
    with AssetResolver(VANILLA_ASSETS, bsa_readers=[]) as resolver:
        build_facegen_nif(info, resolver, nif_path)
        dds_path = build_facetint_dds(info, resolver, out_dir)

    return {
        "info": info,
        "nif_path": nif_path,
        "dds_path": dds_path,
        "ref_nif": REF_FACEGEOM / f"{form_id}.nif",
        "ref_dds": REF_FACETINT / f"{form_id}.dds",
    }


def test_live_pipeline_produces_nif(live_output):
    assert live_output["nif_path"].is_file()
    assert live_output["nif_path"].stat().st_size > 0


def test_live_pipeline_produces_dds(live_output):
    assert live_output["dds_path"].is_file()
    assert live_output["dds_path"].stat().st_size > 0


def test_live_nif_has_expected_shape_set(live_output):
    """The shape set pulled from live records must match what build_fixtures
    extracted into the manifest — same HDPT EditorIDs."""
    _ensure_pynifly()
    from pyn.pynifly import NifFile

    ours = NifFile(str(live_output["nif_path"]))
    ref = NifFile(str(live_output["ref_nif"]))
    assert {s.name for s in ours.shapes} == {s.name for s in ref.shapes}


def test_live_dds_matches_reference_within_tolerance(live_output):
    """The live compositor should reproduce CK's reference face-tint
    within the same tolerance the manifest-driven tests enforce."""
    ours = np.asarray(
        Image.open(live_output["dds_path"]).convert("RGBA"), dtype=np.int16)
    ref = np.asarray(
        Image.open(live_output["ref_dds"]).convert("RGBA"), dtype=np.int16)
    diff = np.abs(ours - ref)
    for ch, name, tol in [(0, "R", 5.0), (1, "G", 5.0), (2, "B", 5.0)]:
        mean = diff[..., ch].mean()
        assert mean < tol, f"{name} mean {mean:.2f} exceeds {tol}"


def test_live_nif_head_shape_matches_reference(live_output):
    """Dervenin's morphed head verts in the live output should match
    CK's reference within the same tolerance the manifest path enforces."""
    _ensure_pynifly()
    from pyn.pynifly import NifFile

    ours = NifFile(str(live_output["nif_path"]))
    ref = NifFile(str(live_output["ref_nif"]))
    ref_head = ref.shape_dict["MaleHeadWoodElf"]
    our_head = ours.shape_dict["MaleHeadWoodElf"]
    ref_v = np.asarray(ref_head.verts, dtype=np.float32)
    our_v = np.asarray(our_head.verts, dtype=np.float32)
    max_d = float(np.abs(our_v - ref_v).max())
    mean_d = float(np.abs(our_v - ref_v).mean())
    assert max_d < 0.1, f"head max vert diff {max_d:.3f}"
    assert mean_d < 0.01, f"head mean diff {mean_d:.4f}"
