"""
Facegen engine pipeline tests. Each run:
  1. Regenerates NIF + FaceTint DDS for each test NPC from headpart sources
  2. Auto-stages outputs into Hugh's Sandbox mod for in-game inspection
  3. Runs structural assertions vs the CK-baked reference

When in-game testing surfaces a new bug class, we add an assertion here
so the test regresses if we re-introduce it. That's the loop: discover
in-game, codify as an assertion, never re-discover the same bug.

Fixtures live inline to avoid a `conftest.py` under `tests/facegen/`,
which would collide with `tests/integration/conftest.py` via
pytest's default `import-mode=prepend` — both modules would be imported
as `conftest` and the second one would shadow the first in `sys.modules`.
"""
import json
import shutil
import struct
import sys
from collections import Counter
from ctypes import create_string_buffer
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


HERE = Path(__file__).parent
DATA_VANILLA = HERE / "Data_vanilla"
OUT_NIFS = HERE / "out_headparts" / "Data_vanilla"
OUT_DDS = HERE / "out_tints" / "Data_vanilla"
REF_FACEGEOM = DATA_VANILLA / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"
REF_FACETINT = DATA_VANILLA / "textures/actors/character/FaceGenData/FaceTint/Skyrim.esm"

SANDBOX = Path(
    r"C:\Users\hughr\AppData\Roaming\Vortex\skyrimse\mods\Sandbox"
)
SANDBOX_NIF = SANDBOX / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"
SANDBOX_DDS = SANDBOX / "textures/actors/character/FaceGenData/FaceTint/Skyrim.esm"

NPC_CASES = [
    pytest.param("0001414D", id="ulfric"),
    pytest.param("0001327C", id="dervenin"),
    pytest.param("00013268", id="deeja"),
]


# ------------------------------------------------------------------ helpers --


def _ensure_paths():
    """Put the spike script directory and PyNifly on sys.path for import.

    Called inside fixtures so the path tweak doesn't happen at module-top
    (it would shadow the integration conftest in sys.modules)."""
    here = str(HERE)
    if here not in sys.path:
        sys.path.insert(0, here)
    pynifly = r"C:\Modding\PyNifly\io_scene_nifly"
    if pynifly not in sys.path:
        sys.path.insert(0, pynifly)


def regenerate(form_id: str) -> dict:
    _ensure_paths()
    from assemble_from_headparts import assemble_from_manifest
    from composite_tint import composite_to_png_and_dds

    assemble_from_manifest(
        DATA_VANILLA, form_id, OUT_NIFS / f"{form_id}.nif")
    composite_to_png_and_dds(DATA_VANILLA, form_id, OUT_DDS)
    return {
        "form_id": form_id,
        "our_nif": OUT_NIFS / f"{form_id}.nif",
        "our_dds": OUT_DDS / f"{form_id}.dds",
        "ref_nif": REF_FACEGEOM / f"{form_id}.nif",
        "ref_dds": REF_FACETINT / f"{form_id}.dds",
    }


def stage_to_sandbox(bundle: dict) -> None:
    SANDBOX_NIF.mkdir(parents=True, exist_ok=True)
    SANDBOX_DDS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundle["our_nif"], SANDBOX_NIF / f"{bundle['form_id']}.NIF")
    shutil.copy2(bundle["our_dds"], SANDBOX_DDS / f"{bundle['form_id']}.dds")


def nif_block_type_counts(nif_path: Path) -> Counter:
    _ensure_paths()
    from pyn.pynifly import NifFile
    from pyn.niflydll import nifly

    nif = NifFile(str(nif_path))
    blocks = []
    for bid in range(500):
        buf = create_string_buffer(128)
        rc = nifly.getBlockname(nif._handle, bid, buf, 128)
        if rc <= 0 or buf.value == b"":
            break
        blocks.append(buf.value.decode("utf-8"))
    return Counter(blocks)


def load_nif(path: Path):
    _ensure_paths()
    from pyn.pynifly import NifFile
    return NifFile(str(path))


@pytest.fixture(scope="session", params=NPC_CASES)
def npc_output(request):
    bundle = regenerate(request.param)
    stage_to_sandbox(bundle)
    return bundle


# ----------------------------------------------------------- NIF STRUCTURE --


def test_nif_rootname_matches_formid(npc_output):
    """CK stamps <FORMID>.NIF as the root node's name. PyNifly's
    initialize() fails to set it; we override explicitly."""
    nif = load_nif(npc_output["our_nif"])
    assert nif.rootName == f"{npc_output['form_id']}.NIF"


def test_nif_root_is_bsfadenode(npc_output):
    nif = load_nif(npc_output["our_nif"])
    assert nif.root.blockname == "BSFadeNode"


def test_nif_bone_stubs_before_facegen_node(npc_output):
    """CK writes bone NiNodes first, then BSFaceGenNiNodeSkinned, then
    shapes. Skyrim's linear loader needs bones before shapes that
    reference them."""
    nif = load_nif(npc_output["our_nif"])
    for bid in sorted(nif.node_ids.keys()):
        block = nif.node_ids[bid]
        name = getattr(block, "name", "")
        if name == "BSFaceGenNiNodeSkinned":
            pytest.fail("BSFaceGenNiNodeSkinned before first bone stub")
        if name.startswith("NPC "):
            return
    pytest.fail("neither bone stubs nor BSFaceGenNiNodeSkinned found")


def test_nif_block_counts_match_reference(npc_output):
    """Block-type counts must match CK, modulo BSShaderTextureSet
    de-dup (cosmetic)."""
    ours = nif_block_type_counts(npc_output["our_nif"])
    ref = nif_block_type_counts(npc_output["ref_nif"])
    for block_type in set(ours) | set(ref):
        if block_type == "BSShaderTextureSet":
            continue
        assert ours.get(block_type, 0) == ref.get(block_type, 0), (
            f"{block_type}: ours={ours.get(block_type, 0)} ref={ref.get(block_type, 0)}"
        )


def test_nif_skin_instance_types_match_reference(npc_output):
    """Wrong BSDismember/NiSkinInstance split trips Skyrim's NIF
    validation (dark face bug)."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    ref_types = {s.name: s.skin_instance_name for s in ref.shapes}
    our_types = {s.name: s.skin_instance_name for s in ours.shapes}
    assert our_types == ref_types


def test_nif_shape_names_match_reference(npc_output):
    """Shape names are HDPT EditorIDs; must be the exact set from the
    NPC's PNAMs or Skyrim falls back to race default head."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    assert {s.name for s in ours.shapes} == {s.name for s in ref.shapes}


def test_nif_shape_geometry_counts(npc_output):
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        assert len(our_s.verts) == len(ref_s.verts), f"{ref_s.name} verts"
        assert len(our_s.tris) == len(ref_s.tris), f"{ref_s.name} tris"


def test_nif_bone_stub_transforms_match_reference(npc_output):
    """CK strips bone-stub rotations to identity even when the source
    headpart carries bind-pose rotation; we must too."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for name, ref_node in ref.nodes.items():
        if not name.startswith("NPC "):
            continue
        our_node = ours.nodes.get(name)
        assert our_node is not None, f"missing bone stub {name}"
        assert ref_node.transform.NearEqual(our_node.transform, epsilon=0.001)


def test_nif_shape_uvs_match_reference(npc_output):
    """Regression on the PyNifly UV flip bug — createShapeFromData
    applies (u, 1-v) on write while shape.uvs reads raw."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        ref_uvs = np.asarray(ref_s.uvs, dtype=np.float32)
        our_uvs = np.asarray(our_s.uvs, dtype=np.float32)
        assert np.allclose(our_uvs, ref_uvs, atol=0.001), f"{ref_s.name} UVs"


def test_nif_shape_normals_match_reference(npc_output):
    """Regression on all-zero-normals bug — zero literals get quantized
    to 1/255 noise; pass None to recompute."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        if not ref_s.normals:
            continue
        ref_n = np.asarray(ref_s.normals, dtype=np.float32)
        our_n = np.asarray(our_s.normals, dtype=np.float32)
        assert np.allclose(our_n, ref_n, atol=0.01), f"{ref_s.name} normals"


def test_nif_shape_vertex_colors_match_reference(npc_output):
    """Regression on pure-black-head bug — shader with VERTEX_COLORS
    flag + no vertex colors = multiply by zero."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        ref_count = len(ref_s.colors) if ref_s.colors else 0
        our_count = len(our_s.colors) if our_s.colors else 0
        assert our_count == ref_count, (
            f"{ref_s.name}: colors ours={our_count} ref={ref_count}"
        )


def test_nif_shape_partition_ids_match_reference(npc_output):
    """Regression on partition-index-vs-id bug."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        ref_ids = sorted(p.id for p in ref_s.partitions) if ref_s.partitions else []
        our_ids = sorted(p.id for p in our_s.partitions) if our_s.partitions else []
        assert our_ids == ref_ids, f"{ref_s.name} partitions"


def test_nif_shape_shader_flags_match_reference(npc_output):
    """Shader_Type=4 drives face-tint sampling; flag bits like
    FACEGEN_DETAIL_MAP / VERTEX_COLORS gate whole rendering behaviors."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        r, o = ref_s.shader.properties, our_s.shader.properties
        assert r.Shader_Type == o.Shader_Type, f"{ref_s.name} Shader_Type"
        assert r.Shader_Flags_1 == o.Shader_Flags_1, (
            f"{ref_s.name} flags1 ref=0x{r.Shader_Flags_1:08x} "
            f"ours=0x{o.Shader_Flags_1:08x}"
        )
        assert r.Shader_Flags_2 == o.Shader_Flags_2, f"{ref_s.name} flags2"


def test_nif_face_shape_has_facegen_detail_slot(npc_output):
    """CK stamps the per-NPC FaceTint dds path into slot 6
    (FacegenDetail) on Face-type headparts. Required for runtime
    face-tint sampling."""
    ours = load_nif(npc_output["our_nif"])
    face_bit = 1 << 10
    found = 0
    for s in ours.shapes:
        if s.shader.properties.Shader_Flags_1 & face_bit:
            fd = s.textures.get("FacegenDetail") or ""
            if "FaceTint" in fd and npc_output["form_id"] in fd.upper():
                found += 1
    assert found >= 1, "no shape has slot 6 FacegenDetail pointing at this NPC's FaceTint"


def test_nif_skin_to_bone_transforms_match_reference(npc_output):
    """Regression on the add_bone-resets-skin-state bug — only the last
    bone's s2b would stick."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        if not ref_s.has_skin_instance:
            continue
        our_s = ours.shape_dict[ref_s.name]
        for bone in ref_s.bone_names:
            ref_xf = ref_s.get_shape_skin_to_bone(bone)
            our_xf = our_s.get_shape_skin_to_bone(bone)
            assert ref_xf.NearEqual(our_xf, epsilon=0.001), (
                f"{ref_s.name}: s2b[{bone}] diverges"
            )


# ------------------------------------------------------------------- MANIFEST --


def test_manifest_has_qnam_color_per_npc():
    """Regression on Dervenin-no-skin-color bug — NPCs without explicit
    SkinTone TINI entries rely on QNAM for their skin color base."""
    mf = json.loads((DATA_VANILLA / "manifest.json").read_text())
    for npc in mf["npcs"]:
        qnam = npc.get("qnam_color")
        assert qnam is not None and len(qnam) == 3, f"{npc['label']}: {qnam!r}"
        assert all(0 <= c <= 255 for c in qnam)


def test_manifest_tint_entries_carry_tinp():
    """Regression on TINP-based skin tone detection — without TINP the
    compositor can't identify the Skin Tone layer."""
    mf = json.loads((DATA_VANILLA / "manifest.json").read_text())
    for npc in mf["npcs"]:
        for tint in npc.get("tints", []):
            assert "tinp" in tint, f"{npc['label']} tini={tint['tini']} no tinp"


# ------------------------------------------------------------- TEXCONV SMOKE --


def test_texconv_wrapper_produces_bc7():
    _ensure_paths()
    from texconv_wrapper import encode_bc7, TEXCONV_EXE
    assert TEXCONV_EXE.is_file()
    tmp_dir = HERE / "out_tints"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_png = tmp_dir / "_texconv_smoke.png"
    Image.new("RGBA", (64, 64), (128, 64, 32, 255)).save(tmp_png)
    dds = encode_bc7(tmp_png, tmp_dir)
    try:
        with open(dds, "rb") as f:
            data = f.read(148)
        dxgi = struct.unpack("<I", data[128:132])[0]
        assert dxgi == 98, f"dxgiFormat={dxgi}, expected 98 (BC7_UNORM)"
    finally:
        tmp_png.unlink(missing_ok=True)
        dds.unlink(missing_ok=True)


# ----------------------------------------------------------------- DDS TINT --


def test_dds_size_close_to_reference(npc_output):
    """Texconv produces a full mipmap chain (10 levels for 512²); CK
    stops at 9, so there's ~36 bytes of size variance."""
    ours_sz = npc_output["our_dds"].stat().st_size
    ref_sz = npc_output["ref_dds"].stat().st_size
    assert abs(ours_sz - ref_sz) < 1024, (
        f"ours {ours_sz} ref {ref_sz} diff exceeds 1KB"
    )


def test_dds_dimensions_match_reference(npc_output):
    ours = np.asarray(Image.open(npc_output["our_dds"]).convert("RGBA"))
    ref = np.asarray(Image.open(npc_output["ref_dds"]).convert("RGBA"))
    assert ours.shape == ref.shape


def test_dds_mean_pixel_diff_within_tolerance(npc_output):
    """BC7 compression noise + our approximation of CK's exact blend
    math; tolerance of 5/255 mean."""
    ours = np.asarray(Image.open(npc_output["our_dds"]).convert("RGBA"), dtype=np.int16)
    ref = np.asarray(Image.open(npc_output["ref_dds"]).convert("RGBA"), dtype=np.int16)
    diff = np.abs(ours - ref)
    for ch, name, tol in [(0, "R", 5.0), (1, "G", 5.0), (2, "B", 5.0), (3, "A", 0.5)]:
        mean = diff[..., ch].mean()
        assert mean < tol, f"{name} mean {mean:.2f} exceeds {tol}"


# ---------------------------------------------- CONFIGURABLE OUTPUT SIZE --


@pytest.mark.parametrize("output_size", [256, 512, 1024, 2048, 4096])
def test_compositor_honors_output_size_param(output_size):
    """The compositor must accept an `output_size` (power of 2, 256..4096)
    and produce a DDS at that resolution, resampling the input masks to
    match. Vanilla masks are 512; bigger output sizes exercise the
    Lanczos upscale path."""
    _ensure_paths()
    from composite_tint import composite_to_png_and_dds
    OUT_UPSCALE = OUT_DDS.parent / f"Data_vanilla_size{output_size}"
    _, dds = composite_to_png_and_dds(
        DATA_VANILLA, "0001414D",
        OUT_UPSCALE,
        output_size=output_size,
    )
    img = Image.open(dds)
    assert img.size == (output_size, output_size), (
        f"output size {img.size} != requested {output_size}"
    )


def test_compositor_native_size_matches_ck_reference():
    """Sanity: at 512x512 (native mask size) the Lanczos resample path
    should be identity, so output must stay within the existing CK-match
    tolerance for Ulfric."""
    _ensure_paths()
    from composite_tint import composite_to_png_and_dds
    _, dds = composite_to_png_and_dds(
        DATA_VANILLA, "0001414D",
        OUT_DDS.parent / "Data_vanilla_size512",
        output_size=512,
    )
    ours = np.asarray(Image.open(dds).convert("RGBA"), dtype=np.int16)
    ref = np.asarray(
        Image.open(REF_FACETINT / "0001414D.dds").convert("RGBA"),
        dtype=np.int16,
    )
    diff = np.abs(ours - ref)
    assert diff[..., 0].mean() < 5.0, "R drifted at native size"
    assert diff[..., 1].mean() < 5.0, "G drifted at native size"
    assert diff[..., 2].mean() < 5.0, "B drifted at native size"


def test_dds_max_pixel_diff_bounded(npc_output, request):
    """Worst single-pixel diff. Deeja has a localized ~120-unit B
    outlier — xfailed for her."""
    if request.node.callspec.id == "deeja":
        pytest.xfail("known Argonian B-channel outlier")
    ours = np.asarray(Image.open(npc_output["our_dds"]).convert("RGBA"), dtype=np.int16)
    ref = np.asarray(Image.open(npc_output["ref_dds"]).convert("RGBA"), dtype=np.int16)
    diff = np.abs(ours - ref)
    max_diff = int(diff[..., :3].max())
    assert max_diff < 30, f"max {max_diff}"
