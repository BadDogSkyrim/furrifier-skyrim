"""
Phase 1 Step 1b: assemble a facegen nif from the NPC's component
headpart nifs — the real engine path.

Stacks each NPC headpart's shape under a single BSFaceGenNiNodeSkinned
in a fresh output nif. Per Step 0 scout, vanilla SSE headparts are
already BSDynamicTriShape with complete skin data, s2b, shader flags,
partitions, vertex colors etc. — CK's Ctrl-F4 is effectively just
concatenation + shape rename + morph bake.

Two entry points:

- `build_facegen_nif(npc_info, resolver, dst_path)` is the live API
  used by the furrifier pipeline. Takes a dict with the same shape as
  one manifest entry plus an AssetResolver for source-file lookup.
- `assemble_from_manifest(data_root, form_id, dst_path)` is the legacy
  wrapper: reads `manifest.json` under `data_root`, spins up a
  loose-only resolver rooted there, and delegates. Used by tests and
  the CLI.
"""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from .._pyn import ensure_dev_path
ensure_dev_path()
from pyn.pynifly import NifFile
from pyn.structs import TransformBuf
from pyn.nifdefs import PynBufferTypes
from pyn.niflydll import nifly

from .assets import AssetResolver
from .morph import apply_morphs


log = logging.getLogger("furrifier.facegen.assemble")

HERE = Path(__file__).parent
# CLI mode resolves paths relative to the tests fixture tree.
_TEST_FACEGEN_ROOT = Path(__file__).resolve().parents[3] / "tests" / "facegen"
OUT_DIR = _TEST_FACEGEN_ROOT / "out_headparts"


# All the facegen nifs we emit target Skyrim SE. Root is BSFadeNode
# with flags=14 per Step 0 scouting (consistent across every
# CK-generated sample inspected).
_GAME = "SKYRIMSE"
_ROOT_TYPE = "BSFadeNode"
_ROOT_FLAGS = 14


def identity_xform():
    xf = TransformBuf()
    xf.set_identity()
    return xf


def _area_weighted_vertex_normals(verts: np.ndarray,
                                  tris: np.ndarray) -> np.ndarray:
    """Compute per-vertex normals from triangle geometry.

    Each triangle contributes its face normal weighted by area
    (the raw cross product length encodes 2x area) to every vertex
    it touches; result is normalized per-vertex.

    Matches the formula used by most 3D viewers for smooth-shaded
    meshes and matches what we used to rely on PyNifly to produce
    on save — but PyNifly doesn't repair all-zero stored normals,
    and the shape's stored normals are stale after we bake chargen
    morphs into verts. Running this unconditionally fixes both.
    """
    v0 = verts[tris[:, 0]]
    v1 = verts[tris[:, 1]]
    v2 = verts[tris[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)  # area-weighted face normals
    out = np.zeros_like(verts)
    np.add.at(out, tris[:, 0], fn)
    np.add.at(out, tris[:, 1], fn)
    np.add.at(out, tris[:, 2], fn)
    lengths = np.linalg.norm(out, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    return (out / lengths).astype(np.float32)


HDPT_TYPE_FACE = 1  # Only Face-type headparts get the per-NPC FacegenDetail.
HDPT_TYPE_EYES = 2  # Must use NiSkinInstance — see _should_demote.


def copy_shape(dst: NifFile, fg: "NiNode", src_shape, rename_to: str,
               facegen_detail_path: str | None = None,
               verts_override=None,
               texture_overrides: dict[str, str] | None = None) -> "NiShape":
    """Copy one shape from its source nif into `dst` under `fg`, renamed.

    Preserves: verts, tris, uvs, normals, vertex colors, local xform,
    skin (bones + s2b + weights + global_to_skin), shader properties +
    textures, alpha property, partitions.

    verts_override: optional (N, 3) vert array (list of tuples or ndarray)
    to use instead of src_shape.verts — used for morph-baked verts.

    texture_overrides: optional {slot_name: relpath_under_textures/} map
    from HDPT.TNAM → TXST. CK's facegen bake writes these over whatever
    the source headpart nif's shader carried (e.g. eye-color variants)."""
    # UVs pass through untouched as of PyNifly UV fix (2026-04-22).
    # Earlier versions applied (u, 1-v) on write and we had to pre-
    # unflip here to compensate; no longer needed.
    src_uvs = list(src_shape.uvs) if src_shape.uvs else []

    if verts_override is not None:
        verts_np = np.asarray(verts_override, dtype=np.float32)
    else:
        verts_np = np.asarray(src_shape.verts, dtype=np.float32)
    verts = [tuple(float(c) for c in v) for v in verts_np]

    # Normals policy:
    # - Source has no normals at all, or all-zero normals: the source
    #   is using a model-space normal texture for its lighting, not
    #   vertex normals. Pass None so our output does the same. Don't
    #   fabricate geometry normals — they'd conflict with the
    #   model-space map at render time.
    # - Source has real normals: they describe the un-morphed source
    #   geometry, and morph baking has moved verts since. Recompute
    #   area-weighted normals from the current geometry so the output
    #   lighting matches the morphed shape.
    src_normals = src_shape.normals
    has_real_normals = (src_normals and not all(
        all(abs(c) < 1e-6 for c in n) for n in src_normals))
    if has_real_normals:
        tris_np = np.asarray(src_shape.tris, dtype=np.uint32)
        normals_np = _area_weighted_vertex_normals(verts_np, tris_np)
        normals_arg = [tuple(float(c) for c in n) for n in normals_np]
    else:
        normals_arg = None

    new_shape = dst.createShapeFromData(
        rename_to,
        verts,
        list(src_shape.tris),
        src_uvs,
        normals_arg,
        use_type=PynBufferTypes.BSDynamicTriShapeBufType,
        parent=fg,
    )

    new_shape.transform = src_shape.transform

    if src_shape.colors:
        new_shape.set_colors(list(src_shape.colors))

    new_shape.skin()

    if src_shape.has_global_to_skin:
        new_shape.set_global_to_skin(src_shape.global_to_skin)

    # add_bone resets skin data — add all first, then set s2b, then weights.
    for bone_name in src_shape.bone_names:
        new_shape.add_bone(bone_name)
    for bone_name in src_shape.bone_names:
        new_shape.set_skin_to_bone_xform(
            bone_name, src_shape.get_shape_skin_to_bone(bone_name))
    for bone_name, vw in src_shape.bone_weights.items():
        new_shape.setShapeWeights(bone_name, vw)

    # Shader — copy ctypes buffer wholesale, then the texture set.
    src_sh = src_shape.shader
    src_sh.properties  # lazy-load
    new_sh = new_shape.shader
    if src_sh._properties is not None:
        new_sh._properties = src_sh._properties.copy()
    new_shape.save_shader_attributes()

    for slot, path in src_shape.textures.items():
        if path:
            new_shape.set_texture(slot, path)
    # HDPT.TNAM → TXST overrides. Stored without the leading "textures\"
    # segment; CK's facegen bake writes them with that prefix in the nif.
    if texture_overrides:
        for slot, rel in texture_overrides.items():
            if not rel:
                continue
            if not rel.lower().startswith("textures\\") and not rel.lower().startswith("textures/"):
                rel = "textures\\" + rel.lstrip("\\/")
            new_shape.set_texture(slot, rel)
    # Face-type headparts get the per-NPC FacegenDetail tint path stamped
    # in during CK's Ctrl-F4. Mouth can carry the same shader flag but CK
    # leaves its slot empty — so we gate off HDPT type, not the flag.
    if facegen_detail_path:
        new_shape.set_texture("FacegenDetail", facegen_detail_path)
    new_shape.save_shader_attributes()

    if src_shape.has_alpha_property:
        new_shape.has_alpha_property = True
        src_alpha = src_shape.alpha_property
        if src_alpha and src_alpha._properties is not None:
            new_shape._alpha._properties = src_alpha._properties.copy()
        new_shape.save_alpha_property()

    # partition_tris gives indices; set_partitions wants IDs.
    if src_shape.partitions:
        ids_per_tri = [src_shape.partitions[i].id for i in src_shape.partition_tris]
        new_shape.set_partitions(src_shape.partitions, ids_per_tri)

    return new_shape


def build_facegen_nif(npc_info: dict, resolver: AssetResolver,
                      dst_path: Path) -> NifFile:
    """Assemble a facegen nif from `npc_info` and write it to `dst_path`.

    `npc_info` has the same shape as one `manifest.json` entry — form_id,
    base_plugin, headparts (list of {hdpt_edid, hdpt_type, source_nif,
    race_tri, chargen_tri, behavior_tri, textures}), race_edid, nam9,
    weight, nama.

    Source nif / tri paths are Data-relative; they're resolved via
    `resolver`. Missing headpart nifs are warned and skipped (the
    resulting nif just omits those shapes); missing tris are tolerated
    downstream by `apply_morphs`.
    """
    form_id = npc_info["form_id"]
    base_plugin = npc_info["base_plugin"]

    print(f"[npc] 0x{form_id} ({npc_info.get('npc_edid')})")
    print(f"[npc] {len(npc_info['headparts'])} headparts")

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()

    dst = NifFile()
    # CK writes `<FORMID>.NIF` as the root node's name. PyNifly's default is
    # 'Scene Root' which would show up wrong. Mirror CK exactly.
    root_name = f"{form_id}.NIF"
    dst.initialize(_GAME, str(dst_path),
                   root_type=_ROOT_TYPE,
                   root_name=root_name)
    # initialize()'s root_name argument isn't actually persisted by PyNifly's
    # createNif; set it explicitly via the NiNode.name setter which updates
    # the NIF string table.
    dst.root.name = root_name
    dst.root.flags = _ROOT_FLAGS
    try:
        dst.root.write_properties()
    except Exception as e:
        print(f"[warn] root.write_properties failed: {e}")

    # Open each headpart source nif once; gather shapes + per-bone bind-pose
    # TRANSLATIONS from the source nif's top-level NiNode stubs. CK's Ctrl-F4
    # keeps the bones' translations but zeroes their rotation to identity in
    # the facegen output (even when the source headpart nif has non-identity
    # bind-pose rotation, e.g. Spine2's ~7.7° X bend). Match that.
    sources = []
    bone_xforms: dict[str, TransformBuf] = {}
    for hp in npc_info["headparts"]:
        src_rel = hp["source_nif"]
        src_path = resolver.resolve(src_rel)
        if src_path is None:
            log.warning("[%s] source nif missing: %s",
                        npc_info.get("npc_edid") or form_id, src_rel)
            continue
        src_nif = NifFile(str(src_path))
        if len(src_nif.shapes) != 1:
            log.warning("[%s] %s has %d shapes; taking first",
                        hp["hdpt_edid"], src_rel, len(src_nif.shapes))
        src_shape = src_nif.shapes[0]

        # Phase 4: bake race + chargen + weight morphs into the verts.
        # Resolve each tri relpath; warn on "listed but missing" (the
        # resolver collapses "no tri" and "missing tri" into None, so
        # we re-introduce the distinction here).
        def _resolve_tri(relpath, kind):
            if not relpath:
                return None
            found = resolver.resolve(relpath)
            if found is None:
                log.warning("[%s] %s tri missing: %s",
                            hp["hdpt_edid"], kind, relpath)
            return found

        race_tri_path = _resolve_tri(hp.get("race_tri"), "race")
        chargen_tri_path = _resolve_tri(hp.get("chargen_tri"), "chargen")
        behavior_tri_path = _resolve_tri(hp.get("behavior_tri"), "behavior")
        morphed_verts = apply_morphs(
            np.asarray(src_shape.verts, dtype=np.float32),
            race_tri_path=race_tri_path,
            race_edid=npc_info.get("race_edid"),
            chargen_tri_path=chargen_tri_path,
            nam9=npc_info.get("nam9"),
            behavior_tri_path=behavior_tri_path,
            weight=npc_info.get("weight"),
            nama=npc_info.get("nama"),
            shape_name=hp["hdpt_edid"],
        )
        sources.append((hp["hdpt_edid"], hp.get("hdpt_type"),
                        src_nif, src_shape, morphed_verts,
                        hp.get("textures") or {}))
        for bone in src_shape.bone_names:
            if bone not in bone_xforms and bone in src_nif.nodes:
                src_xf = src_nif.nodes[bone].transform
                stub = TransformBuf()
                stub.set_identity()
                stub.translation = src_xf.translation
                bone_xforms[bone] = stub

    # If nothing resolved, there's no valid facegen to write. Better
    # to raise here — the caller's per-NPC try/except turns it into a
    # clear "skipped N: no source headparts resolved" log line than
    # silently writing a shape-less nif the game will bounce.
    if not sources:
        raise FileNotFoundError(
            f"no headpart source nifs resolved for {form_id}")

    # CK orders top-level children as: bone stubs first, then
    # BSFaceGenNiNodeSkinned. Skyrim's NIF loader parses linearly — shapes
    # at the end reference bones by name, so bones must be declared before
    # the shapes that use them. Match CK's order exactly.
    for bone in sorted(bone_xforms):
        dst.add_node(bone, bone_xforms[bone], parent=dst.root)
    fg = dst.add_node("BSFaceGenNiNodeSkinned", identity_xform(), parent=dst.root)

    facegen_detail_rel = (
        f"textures\\actors\\character\\FaceGenData\\FaceTint\\"
        f"{base_plugin}\\{form_id}.dds"
    )
    for edid, hdpt_type, _src_nif, src_shape, morphed_verts, tex_over in sources:
        print(f"[copy] {edid} (type={hdpt_type}, source shape "
              f"'{src_shape.name}', {len(src_shape.verts)} verts)")
        face_tint = facegen_detail_rel if hdpt_type == HDPT_TYPE_FACE else None
        copy_shape(dst, fg, src_shape, rename_to=edid,
                   facegen_detail_path=face_tint,
                   verts_override=morphed_verts,
                   texture_overrides=tex_over)

    # Demote pass — PyNifly's `skin()` always creates
    # BSDismemberSkinInstance, but eyes and other non-dismemberable
    # parts need plain NiSkinInstance (Skyrim rejects the wrong type
    # and falls back to the race default head). Classifying by HDPT
    # type (not the source nif's own type) is important because some
    # YAS meshes ship as BSDismember even for eyes/brows.
    # Demote to NiSkinInstance when:
    #   - the source nif uses NiSkinInstance (vanilla eye/mouth/brows/
    #     scar meshes do; CK preserves the source type), OR
    #   - the HDPT type is Eyes (2). Eyes *must* use NiSkinInstance
    #     regardless of source — some YAS eye meshes ship as
    #     BSDismember (upstream bug) and Skyrim's facegen morph-data
    #     pipeline crashes dereferencing dismember partitions on an
    #     eye shape at NPC load time.
    names_to_demote = {
        edid for edid, hdpt_type, _src_nif, src_shape, _morphed, _tex in sources
        if hdpt_type == HDPT_TYPE_EYES
           or src_shape.skin_instance_name == "NiSkinInstance"
    }
    if names_to_demote:
        for s in dst.shapes:
            if s.name in names_to_demote:
                nifly.demoteSkinInstance(dst._handle, s._handle)

    dst.save()

    print(f"[save] {dst_path} ({os.path.getsize(dst_path)} bytes)")
    return dst


def assemble_from_manifest(data_root: Path, form_id: str, dst_path: Path) -> NifFile:
    """Legacy manifest-driven entry point. Loads manifest.json, finds
    the NPC by form_id, and calls `build_facegen_nif` through a
    loose-only resolver rooted at `data_root`."""
    manifest = json.loads((data_root / "manifest.json").read_text())
    entry = next((n for n in manifest["npcs"] if n["form_id"] == form_id), None)
    if entry is None:
        raise SystemExit(f"no NPC with form_id {form_id} in {data_root}/manifest.json")

    with AssetResolver(data_root, bsa_readers=[]) as resolver:
        return build_facegen_nif(entry, resolver, dst_path)


if __name__ == "__main__":
    data_root_name = sys.argv[1] if len(sys.argv) > 1 else "Data_vanilla"
    form_id = sys.argv[2] if len(sys.argv) > 2 else "0001414D"

    data_root = _TEST_FACEGEN_ROOT / data_root_name
    dst = OUT_DIR / data_root_name / f"{form_id}.nif"
    assemble_from_manifest(data_root, form_id, dst)
