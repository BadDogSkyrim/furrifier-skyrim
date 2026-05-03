"""Skin-tint color baked into the nif must match the NPC's QNAM.

CK's Ctrl-F4 stamps the actor's skin-tone color (QNAM, 3 floats 0-1)
into every Shader_Type=Skin_Tint(5) shape's `skinTintColor` shader-
buffer field. The engine reads it from the nif at render time —
not from the NPC record at runtime — so a mismatched skinTintColor
breaks visible tinting (BDMino horns rendered gray instead of the
intended dark base, issue #11).

These tests use the existing Data_vanilla fixture, where the CK
reference nifs already encode the rule. Regression-grade: if anyone
breaks the QNAM→skinTintColor copy in the assembler, the assertion
diffs against the authoritative CK output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
DATA_VANILLA = HERE / "Data_vanilla"
OUT_NIFS = HERE / "out_headparts" / "Data_vanilla"
REF_FACEGEOM = DATA_VANILLA / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"


def _ensure_paths():
    pynifly = r"C:\Modding\PyNifly\io_scene_nifly"
    if pynifly not in sys.path:
        sys.path.insert(0, pynifly)


def _shader_props(shape):
    sh = shape.shader
    sh.properties  # lazy-load
    return sh._properties


# Fixture NPCs whose CK-reference nif contains at least one Skin_Tint shape.
# (Verified by scanning Data_vanilla — Deeja's hair, Dervenin/Ulfric's gash
# marks all use Shader_Type=5.)
SKIN_TINT_CASES = [
    pytest.param("00013268", id="deeja"),
    pytest.param("0001327C", id="dervenin"),
    pytest.param("0001414D", id="ulfric"),
]


@pytest.mark.parametrize("form_id", SKIN_TINT_CASES)
def test_skin_tint_color_matches_qnam(form_id):
    """For every Skin_Tint(5) shape in the assembled nif, skinTintColor
    must equal qnam_color/255 (component-wise, exactly to float precision)
    — same rule CK applies."""
    _ensure_paths()
    from furrifier.facegen.assemble import assemble_from_manifest
    from pyn.pynifly import NifFile

    manifest = json.loads((DATA_VANILLA / "manifest.json").read_text())
    entry = next(n for n in manifest["npcs"] if n["form_id"] == form_id)
    qnam = entry["qnam_color"]
    expected = (qnam[0] / 255.0, qnam[1] / 255.0, qnam[2] / 255.0)

    out_nif = OUT_NIFS / f"{form_id}.nif"
    assemble_from_manifest(DATA_VANILLA, form_id, out_nif)

    nif = NifFile(str(out_nif))
    skin_tint_shapes = [s for s in nif.shapes
                        if _shader_props(s).Shader_Type == 5]
    assert skin_tint_shapes, (
        f"{form_id}: test premise broken — no Skin_Tint shapes in "
        f"assembled nif"
    )
    for s in skin_tint_shapes:
        got = tuple(_shader_props(s).skinTintColor)
        assert got == pytest.approx(expected, abs=1e-6), (
            f"{form_id} shape {s.name!r}: skinTintColor={got} "
            f"expected ≈{expected} (from QNAM={qnam})"
        )


@pytest.mark.parametrize("form_id", SKIN_TINT_CASES)
def test_skin_tint_color_matches_ck_reference(form_id):
    """Cross-check: our nif's Skin_Tint shapes have the same skinTintColor
    as the CK-baked reference for the same shape. Catches drift if the
    QNAM-bake formula ever diverges from CK."""
    _ensure_paths()
    from furrifier.facegen.assemble import assemble_from_manifest
    from pyn.pynifly import NifFile

    out_nif = OUT_NIFS / f"{form_id}.nif"
    ref_nif_path = REF_FACEGEOM / f"{form_id}.nif"
    assemble_from_manifest(DATA_VANILLA, form_id, out_nif)

    ours = {s.name: _shader_props(s) for s in NifFile(str(out_nif)).shapes}
    ref = {s.name: _shader_props(s) for s in NifFile(str(ref_nif_path)).shapes}

    skin_tint_names = [name for name, p in ref.items() if p.Shader_Type == 5]
    assert skin_tint_names, f"{form_id}: ref has no Skin_Tint shapes"
    for name in skin_tint_names:
        assert name in ours, f"{form_id}: shape {name!r} missing from our output"
        ref_color = tuple(ref[name].skinTintColor)
        our_color = tuple(ours[name].skinTintColor)
        assert our_color == pytest.approx(ref_color, abs=1e-6), (
            f"{form_id} shape {name!r}: skinTintColor={our_color} "
            f"vs CK reference {ref_color}"
        )


def test_non_skin_tint_shapes_keep_source_default():
    """Shapes with shader types other than Skin_Tint(5) must NOT be
    overwritten — their skinTintColor stays at whatever the source
    headpart nif carried (typically [1.0, 1.0, 1.0] = neutral)."""
    _ensure_paths()
    from furrifier.facegen.assemble import assemble_from_manifest
    from pyn.pynifly import NifFile

    form_id = "00013268"  # deeja
    out_nif = OUT_NIFS / f"{form_id}.nif"
    ref_nif_path = REF_FACEGEOM / f"{form_id}.nif"
    assemble_from_manifest(DATA_VANILLA, form_id, out_nif)

    ours = {s.name: _shader_props(s) for s in NifFile(str(out_nif)).shapes}
    ref = {s.name: _shader_props(s) for s in NifFile(str(ref_nif_path)).shapes}

    non_skin_tint = [name for name, p in ref.items() if p.Shader_Type != 5]
    assert non_skin_tint
    for name in non_skin_tint:
        if name not in ours:
            continue
        ref_color = tuple(ref[name].skinTintColor)
        our_color = tuple(ours[name].skinTintColor)
        assert our_color == pytest.approx(ref_color, abs=1e-6), (
            f"non-skin-tint shape {name!r}: skinTintColor changed "
            f"({our_color} vs CK {ref_color})"
        )
