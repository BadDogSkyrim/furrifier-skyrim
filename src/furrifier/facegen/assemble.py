"""
Phase 1 Step 1b: assemble a facegen nif from the NPC's component
headpart nifs — the real engine path.

Reads manifest.json from a fixture Data folder, locates the NPC by form
ID, opens each source headpart nif, extracts its shape, renames the shape
to the HDPT EditorID, and stacks them all under a single
BSFaceGenNiNodeSkinned in a fresh output nif.

Per Step 0 scout, vanilla SSE headparts are already BSDynamicTriShape
with complete skin data, s2b, shader flags, partitions, vertex colors
etc. CK's Ctrl-F4 is effectively just concatenation + shape rename.

Usage:
    python assemble_from_headparts.py                       # Ulfric vanilla
    python assemble_from_headparts.py Data_vanilla 0001327C # Dervenin
    python assemble_from_headparts.py Data_furry   00013255 # Addvar
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, r"C:\Modding\PyNifly\io_scene_nifly")
from pyn.pynifly import NifFile
from pyn.structs import TransformBuf
from pyn.nifdefs import PynBufferTypes
from pyn.niflydll import nifly


HERE = Path(__file__).parent
# CLI mode resolves paths relative to the tests fixture tree.
_TEST_FACEGEN_ROOT = Path(__file__).resolve().parents[3] / "tests" / "facegen"
OUT_DIR = _TEST_FACEGEN_ROOT / "out_headparts"


def identity_xform():
    xf = TransformBuf()
    xf.set_identity()
    return xf


HDPT_TYPE_FACE = 1  # Only Face-type headparts get the per-NPC FacegenDetail.


def copy_shape(dst: NifFile, fg: "NiNode", src_shape, rename_to: str,
               facegen_detail_path: str | None = None) -> "NiShape":
    """Copy one shape from its source nif into `dst` under `fg`, renamed.

    Preserves: verts, tris, uvs, normals, vertex colors, local xform,
    skin (bones + s2b + weights + global_to_skin), shader properties +
    textures, alpha property, partitions."""
    # UVs: createShapeFromData applies (u, 1-v) on write; pre-unflip.
    src_uvs = [(u, 1.0 - v) for u, v in src_shape.uvs] if src_shape.uvs else []
    # All-zero normals mean "recompute from geometry"; don't pass literals.
    src_normals = src_shape.normals
    if src_normals and all(all(abs(c) < 1e-6 for c in n) for n in src_normals):
        src_normals = None

    new_shape = dst.createShapeFromData(
        rename_to,
        list(src_shape.verts),
        list(src_shape.tris),
        src_uvs,
        list(src_normals) if src_normals else None,
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


def assemble_from_manifest(data_root: Path, form_id: str, dst_path: Path) -> NifFile:
    manifest = json.loads((data_root / "manifest.json").read_text())
    entry = next((n for n in manifest["npcs"] if n["form_id"] == form_id), None)
    if entry is None:
        raise SystemExit(f"no NPC with form_id {form_id} in {data_root}/manifest.json")

    print(f"[npc] {entry['label']} 0x{entry['form_id']} ({entry['npc_edid']})")
    print(f"[npc] {len(entry['headparts'])} headparts")

    # Reference facegen, used only to seed the output nif's root metadata
    # (game, root block type, root name, root flags).
    ref_path = data_root / entry["facegen_nif"]
    ref = NifFile(str(ref_path))

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()
    dst = NifFile()
    # CK writes `<FORMID>.NIF` as the root node's name. PyNifly's default is
    # 'Scene Root' which would show up wrong. Mirror CK exactly.
    root_name = f"{entry['form_id']}.NIF"
    dst.initialize(ref.game, str(dst_path),
                   root_type=ref.root.blockname,
                   root_name=root_name)
    # initialize()'s root_name argument isn't actually persisted by PyNifly's
    # createNif; set it explicitly via the NiNode.name setter which updates
    # the NIF string table.
    dst.root.name = root_name
    dst.root.flags = ref.root.flags
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
    for hp in entry["headparts"]:
        src_rel = hp["source_nif"]
        src_path = data_root / src_rel
        if not src_path.is_file():
            # Case-insensitive fallback
            parent = src_path.parent
            match = [p for p in parent.iterdir() if p.name.lower() == src_path.name.lower()]
            if match:
                src_path = match[0]
            else:
                print(f"  [MISSING] {src_rel}")
                continue
        src_nif = NifFile(str(src_path))
        if len(src_nif.shapes) != 1:
            print(f"  [WARN] {src_rel} has {len(src_nif.shapes)} shapes; taking first")
        src_shape = src_nif.shapes[0]
        sources.append((hp["hdpt_edid"], hp.get("hdpt_type"), src_nif, src_shape))
        for bone in src_shape.bone_names:
            if bone not in bone_xforms and bone in src_nif.nodes:
                src_xf = src_nif.nodes[bone].transform
                stub = TransformBuf()
                stub.set_identity()
                stub.translation = src_xf.translation
                bone_xforms[bone] = stub

    # CK orders top-level children as: bone stubs first, then
    # BSFaceGenNiNodeSkinned. Skyrim's NIF loader parses linearly — shapes
    # at the end reference bones by name, so bones must be declared before
    # the shapes that use them. Match CK's order exactly.
    for bone in sorted(bone_xforms):
        dst.add_node(bone, bone_xforms[bone], parent=dst.root)
    fg = dst.add_node("BSFaceGenNiNodeSkinned", identity_xform(), parent=dst.root)

    facegen_detail_rel = (
        f"textures\\actors\\character\\FaceGenData\\FaceTint\\"
        f"{entry['base_plugin']}\\{entry['form_id']}.dds"
    )
    for edid, hdpt_type, _src_nif, src_shape in sources:
        print(f"[copy] {edid} (type={hdpt_type}, source shape "
              f"'{src_shape.name}', {len(src_shape.verts)} verts)")
        face_tint = facegen_detail_rel if hdpt_type == HDPT_TYPE_FACE else None
        copy_shape(dst, fg, src_shape, rename_to=edid,
                   facegen_detail_path=face_tint)

    dst.save()

    # Demote pass — must happen AFTER the initial save, re-open the file,
    # demote shapes whose source used plain NiSkinInstance, save again.
    # PyNifly's in-flow save path always writes BSDismember even after a
    # pre-save demote() call; only post-save + re-open + demote + re-save
    # actually persists the change. Mismatching skin-instance type trips
    # Skyrim's NIF-vs-NPC validation and falls back to race default head.
    names_to_demote = {
        edid for edid, _type, _src_nif, src_shape in sources
        if src_shape.skin_instance_name == "NiSkinInstance"
    }
    if names_to_demote:
        reopened = NifFile(str(dst_path))
        for s in reopened.shapes:
            if s.name in names_to_demote:
                nifly.demoteSkinInstance(reopened._handle, s._handle)
        reopened.save()

    print(f"[save] {dst_path} ({os.path.getsize(dst_path)} bytes; "
          f"ref {ref_path.stat().st_size} bytes)")
    return dst


if __name__ == "__main__":
    data_root_name = sys.argv[1] if len(sys.argv) > 1 else "Data_vanilla"
    form_id = sys.argv[2] if len(sys.argv) > 2 else "0001414D"

    data_root = _TEST_FACEGEN_ROOT / data_root_name
    dst = OUT_DIR / data_root_name / f"{form_id}.nif"
    assemble_from_manifest(data_root, form_id, dst)
