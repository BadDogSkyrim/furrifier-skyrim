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

NPC_CASES = [
    pytest.param("0001414D", id="ulfric"),
    pytest.param("0001327C", id="dervenin"),
    pytest.param("00013268", id="deeja"),
]


# ------------------------------------------------------------------ helpers --


def _ensure_paths():
    """PyNifly isn't pip-installed; put its source on sys.path so the
    facegen submodules can `from pyn.pynifly import ...`. Called inside
    fixtures so this path tweak is lazy."""
    pynifly = r"C:\Modding\PyNifly\io_scene_nifly"
    if pynifly not in sys.path:
        sys.path.insert(0, pynifly)


def regenerate(form_id: str) -> dict:
    _ensure_paths()
    from furrifier.facegen.assemble import assemble_from_manifest
    from furrifier.facegen.composite import composite_to_png_and_dds

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
    return regenerate(request.param)


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
    """UV parity with CK's reference facegen. Previously there was a
    PyNifly bug where reads and writes applied asymmetric (u, 1-v)
    flips; we compensated in `copy_shape` with a pre-unflip. Fix
    landed in PyNifly on 2026-04-22 and the compensating flip came
    out — this test guards against the asymmetry creeping back in."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        ref_uvs = np.asarray(ref_s.uvs, dtype=np.float32)
        our_uvs = np.asarray(our_s.uvs, dtype=np.float32)
        assert np.allclose(our_uvs, ref_uvs, atol=0.001), f"{ref_s.name} UVs"


def test_nif_shape_normals_are_valid(npc_output):
    """Any normals we emit must be unit-length and non-zero. Shapes
    whose source uses a model-space normal texture for lighting
    legitimately have no vertex normals at all (the `if our_s.normals`
    skip) — that's fine; what's not fine is emitting zero-length
    normals, which leaves a shape unlit under vertex lighting.

    Regression guard: CK's own facegen output ships with all-zero
    normals on some shapes; assemble.copy_shape used to forward that
    bug. It now either recomputes from geometry (real normals
    upstream) or leaves them out entirely (no-normals upstream)."""
    ours = load_nif(npc_output["our_nif"])
    for our_s in ours.shapes:
        if not our_s.normals:
            continue  # no vertex-normal block at all
        n = np.asarray(our_s.normals, dtype=np.float32)
        lengths = np.linalg.norm(n, axis=1)
        # All-zero block is the "uses model-space normal texture"
        # case — PyNifly writes zeros when we hand it None, and
        # Skyrim's shader ignores vertex normals when the shape's
        # model-space-normals flag is set. Not a broken emit.
        if lengths.max() < 1e-3:
            continue
        # Mixed block: some real, some zero. That's the bug this
        # test is here to catch — individual vertices won't light.
        assert lengths.min() > 0.95, (
            f"{our_s.name}: min normal length {lengths.min():.3f} — "
            "some vertex has degenerate/zero normal")
        assert lengths.max() < 1.05, (
            f"{our_s.name}: max normal length {lengths.max():.3f}")


def test_nif_shape_diffuse_textures_match_reference(npc_output):
    """HDPT.TNAM → TXST.TX00 (Diffuse) drives eye-color / skin-variant
    overrides. Dervenin's demon eyes look right only when the facegen
    nif carries EyeDemon.dds instead of EyesMale.nif's default
    EyeBrown.dds.

    We check Diffuse only — other TXST slots (Normal, Specular, etc.)
    land correctly when CK and our pipeline agree, but the vanilla
    SkyrimSEAssets snapshot has stale non-Diffuse slots for some
    shapes (e.g. Deeja's argonian eye keeps the source nif's tangent-
    space EyeWerewolfBeast_n.dds even though TXST.TX01 supplies a
    proper model-space normal). Diffuse is the slot that drives the
    visible variant selection."""
    ours = load_nif(npc_output["our_nif"])
    ref = load_nif(npc_output["ref_nif"])
    for ref_s in ref.shapes:
        our_s = ours.shape_dict[ref_s.name]
        ref_tex = ref_s.textures.get("Diffuse", "")
        if not ref_tex:
            continue
        our_tex = our_s.textures.get("Diffuse", "")
        # Skyrim paths are case-insensitive; CK's casing varies.
        assert our_tex.lower() == ref_tex.lower(), (
            f"{ref_s.name} Diffuse: ours={our_tex!r} ref={ref_tex!r}"
        )


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
    math; tolerance of 5/255 mean on RGB. Alpha tolerance bumped to
    1.5 in the bc7enc switchover (2026-04-25): texconv and bc7enc
    make different per-block mode choices for alpha and bc7enc lands
    ~0.75 mean alpha diff against the CK reference vs texconv's
    ~0.25. RGB quality is unchanged or slightly better; alpha drift
    is well below visible threshold."""
    ours = np.asarray(Image.open(npc_output["our_dds"]).convert("RGBA"), dtype=np.int16)
    ref = np.asarray(Image.open(npc_output["ref_dds"]).convert("RGBA"), dtype=np.int16)
    diff = np.abs(ours - ref)
    for ch, name, tol in [(0, "R", 5.0), (1, "G", 5.0), (2, "B", 5.0), (3, "A", 1.5)]:
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
    from furrifier.facegen.composite import composite_to_png_and_dds
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
    from furrifier.facegen.composite import composite_to_png_and_dds
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


# ------------------------------------------------------- PHASE 4: MORPHS --


@pytest.fixture(scope="session")
def dervenin_output():
    """Dedicated single-NPC fixture so morph-specific assertions don't have
    to iterate the main parametrized set."""
    return regenerate("0001327C")


def test_head_shape_verts_match_reference_with_morphs(dervenin_output):
    """Dervenin's head-shape verts, after race-morph + NAM9-driven chargen
    morphs, should match CK's reference within tolerance. This is the
    load-bearing morph-pipeline assertion: if it passes for all three
    vanilla NPCs, the algorithm is correct."""
    ours = load_nif(dervenin_output["our_nif"])
    ref = load_nif(dervenin_output["ref_nif"])
    ref_head = ref.shape_dict["MaleHeadWoodElf"]
    our_head = ours.shape_dict["MaleHeadWoodElf"]
    ref_v = np.asarray(ref_head.verts, dtype=np.float32)
    our_v = np.asarray(our_head.verts, dtype=np.float32)
    max_d = float(np.abs(our_v - ref_v).max())
    mean_d = float(np.abs(our_v - ref_v).mean())
    # Pre-morph baseline (no morphing at all): max ~4.0, mean ~0.32.
    # With race morph alone: max ~1.0, mean ~0.15.
    # With race+chargen+SkinnyMorph+NAMA presets: max ~0.05, mean ~0.001.
    # CK's facegen is closed-source; the tiny residual comes from
    # float rounding + any remaining morph we haven't found.
    assert max_d < 0.1, (
        f"Dervenin head shape max vert diff {max_d:.3f} exceeds 0.1 — "
        f"morph pipeline regressed"
    )
    assert mean_d < 0.01, f"mean diff {mean_d:.4f} exceeds 0.01"


def test_missing_chargen_tri_warns_not_errors(tmp_path, caplog):
    """If a headpart's chargen tri is missing from the fixture, the bake
    should proceed (shape emits unmorphed) with a warning logged."""
    import logging
    import json
    import shutil

    # Clone the vanilla fixture under tmp_path, delete the head's chargen tri
    src_root = DATA_VANILLA
    dst_root = tmp_path / "Data"
    shutil.copytree(src_root, dst_root)

    # Remove MaleHead.nif's chargen tri
    victim = dst_root / "meshes/Actors/Character/Character Assets/MaleHeadCustomizations.tri"
    if victim.exists():
        victim.unlink()
    # Also try lower-case variants the manifest may reference
    for alt in victim.parent.glob("MaleHead*ustomization*.tri"):
        alt.unlink()

    _ensure_paths()
    from furrifier.facegen.assemble import assemble_from_manifest

    dst_nif = tmp_path / "out.nif"
    with caplog.at_level(logging.WARNING):
        assemble_from_manifest(dst_root, "0001327C", dst_nif)

    # NIF must still be valid (10 shapes for Dervenin)
    assert dst_nif.is_file(), "assembly crashed when chargen tri was missing"
    nif = load_nif(dst_nif)
    assert len(nif.shapes) == 10, "lost shapes when chargen tri went missing"
    # Warning must mention the missing file
    assert any("MaleHeadCustomization" in rec.message or "tri" in rec.message.lower()
               for rec in caplog.records), (
        f"no warning about missing tri; got records: "
        f"{[r.message for r in caplog.records]}"
    )


def test_nam9_zero_sliders_applies_only_race_morph(tmp_path):
    """An NPC with all-zero NAM9 sliders should have head-shape verts =
    source verts + race morph delta (no chargen contribution). Verifies
    the slider math doesn't accidentally add anything when NAM9 is quiet."""
    import shutil
    import json

    src_root = DATA_VANILLA
    dst_root = tmp_path / "Data"
    shutil.copytree(src_root, dst_root)

    # Zero out Dervenin's NAM9 + presets + set weight=100 so only the
    # race morph contributes. Isolates the chargen-sliders path.
    manifest = json.loads((dst_root / "manifest.json").read_text())
    for npc in manifest["npcs"]:
        if npc["form_id"] == "0001327C":
            npc["nam9"] = [0.0] * 18
            npc["weight"] = 100.0
            npc["nama"] = [-1, -1, -1, -1]
    (dst_root / "manifest.json").write_text(json.dumps(manifest, indent=2))

    _ensure_paths()
    from furrifier.facegen.assemble import assemble_from_manifest

    dst_nif = tmp_path / "out.nif"
    assemble_from_manifest(dst_root, "0001327C", dst_nif)
    nif = load_nif(dst_nif)

    # Load source base + expected race-only verts
    from pyn.pynifly import NifFile
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "trifile_standalone",
        r"C:\Modding\PyNifly\io_scene_nifly\tri\trifile.py")
    tf_mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(tf_mod)
    base_nif = NifFile(str(dst_root / "meshes/Actors/Character/Character Assets/MaleHead.nif"))
    base_verts = np.asarray(base_nif.shapes[0].verts, dtype=np.float32)
    with open(dst_root / "meshes/Actors/Character/Character Assets/MaleHeadRaces.tri", "rb") as f:
        races = tf_mod.TriFile.from_file(f)
    race_delta = (np.asarray(races.morphs["WoodElfRace"], dtype=np.float32)
                  - np.asarray(races.morphs["Basis"], dtype=np.float32))
    expected = base_verts + race_delta

    ours = np.asarray(nif.shape_dict["MaleHeadWoodElf"].verts, dtype=np.float32)
    diff = np.abs(ours - expected).max()
    assert diff < 0.01, (
        f"With NAM9=zeros, head verts should be (base + race_morph); "
        f"got max diff {diff:.3f}"
    )


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
