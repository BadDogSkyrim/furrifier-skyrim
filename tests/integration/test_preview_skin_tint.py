"""Skin-tint plumbing into the live-preview material.

Qt's PrincipledMaterial multiplies `baseColor` × `baseColorMap`. The
QML scene used to hardcode `baseColor: "white"`, so Skyrim Skin_Tint
shapes (e.g. BDMino horn base — issue #11) rendered fully bright in
the previewer even though the nif itself baked a near-black tint into
the shader buffer.

These tests assert the data flow: `load_nif_shapes` exposes each
shape's tint, and `ShapeModel` surfaces it as `baseColor` for QML
to bind to. Render correctness itself is verified visually — Hugh
checks the previewer against an in-game CK reference.
"""
from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
DATA_VANILLA = HERE / "facegen" / "Data_vanilla"
REF_FACEGEOM = DATA_VANILLA / "meshes/actors/character/FaceGenData/FaceGeom/Skyrim.esm"


def _ensure_paths():
    import sys
    pynifly = r"C:\Modding\PyNifly\io_scene_nifly"
    if pynifly not in sys.path:
        sys.path.insert(0, pynifly)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_load_nif_shapes_extracts_skin_tint_color_for_skin_tint_shapes():
    """Shapes with Shader_Type=Skin_Tint(5) must surface the nif's
    skinTintColor (RGB 0-1). Verified against Deeja's CK reference,
    where HairArgonianFemale04 (Skin_Tint) has skinTintColor
    [0.412, 0.514, 0.400] = QNAM (105, 131, 102) / 255."""
    _ensure_paths()
    from furrifier.preview.scene_widget import load_nif_shapes

    shapes = load_nif_shapes(REF_FACEGEOM / "00013268.nif")
    by_name = {s["name"]: s for s in shapes}

    assert "HairArgonianFemale04" in by_name
    rgb = by_name["HairArgonianFemale04"]["skin_tint_color"]
    assert rgb == pytest.approx((105 / 255, 131 / 255, 102 / 255), abs=1e-6)


def test_load_nif_shapes_uses_neutral_for_non_skin_tint():
    """Shapes whose shader is not Skin_Tint must get neutral white —
    baseColor=white preserves the diffuse texture unmodified, matching
    the previewer's pre-fix behavior for these shapes."""
    _ensure_paths()
    from furrifier.preview.scene_widget import load_nif_shapes
    from pyn.pynifly import NifFile

    nif_path = REF_FACEGEOM / "00013268.nif"
    nif = NifFile(str(nif_path))
    non_skin_tint_names = []
    for s in nif.shapes:
        sh = s.shader
        sh.properties
        if sh._properties.Shader_Type != 5:
            non_skin_tint_names.append(s.name)
    assert non_skin_tint_names

    shapes = {s["name"]: s for s in load_nif_shapes(nif_path)}
    for name in non_skin_tint_names:
        assert shapes[name]["skin_tint_color"] == (1.0, 1.0, 1.0), (
            f"non-skin-tint shape {name!r} should default to neutral white"
        )


def test_shape_model_exposes_base_color_as_hex_string(qapp):
    """ShapeModel must expose a `baseColor` Qt property that QML's
    PrincipledMaterial.baseColor accepts (hex string like '#6a8366')."""
    from furrifier.preview.scene_widget import ShapeModel

    # qapp not strictly required for property reads on a parentless
    # QObject, but ShapeModel may pick up Qt context via parent in
    # production — fixture is a defensive precaution.
    _ = qapp
    model = ShapeModel(
        name="test", geometry=None, diffuse_url="",
        skin_tint_color=(105 / 255, 131 / 255, 102 / 255),
    )
    # Hex form is robust across Qt versions and easy to assert on.
    assert model.baseColor.lower() == "#698366"


def test_shape_model_default_base_color_is_white(qapp):
    """When no skin_tint_color is supplied (non-Skin_Tint shapes),
    baseColor defaults to white so the material multiplies diffuse
    by 1.0 — same as the previous hardcoded 'white' literal."""
    from furrifier.preview.scene_widget import ShapeModel

    _ = qapp
    model = ShapeModel(name="test", geometry=None, diffuse_url="")
    assert model.baseColor.lower() == "#ffffff"
