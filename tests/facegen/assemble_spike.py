"""
Phase 1 assembly spike — run against a fixture Data folder.

Given a data-folder root (e.g. Data_vanilla/ or Data_furry/) and an NPC form
ID, reads the reference CK facegen nif and assembles a fresh copy from its
shapes. Output goes to out/<form_id>.nif next to the fixtures.

Usage:
    python assemble_spike.py                         # Ulfric vanilla (default)
    python assemble_spike.py Data_vanilla 0001327C   # Dervenin
    python assemble_spike.py Data_furry   00013255   # Addvar
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, r"C:\Modding\PyNifly\io_scene_nifly")
from pyn.pynifly import NifFile
from pyn.structs import TransformBuf
from pyn.nifdefs import PynBufferTypes


HERE = Path(__file__).parent
OUT_DIR = HERE / "out"


def identity_xform():
    xf = TransformBuf()
    xf.set_identity()
    return xf


def reference_facegen_path(data_root: Path, form_id: int, plugin: str = "Skyrim.esm") -> Path:
    return (data_root / "meshes" / "actors" / "character" /
            "FaceGenData" / "FaceGeom" / plugin / f"{form_id:08X}.nif")


def assemble(src_path: Path, dst_path: Path) -> NifFile:
    src = NifFile(str(src_path))

    src_game = src.game
    src_root_block = src.root.blockname
    src_root_name = src.rootName
    src_root_flags = src.root.flags

    print(f"[src] {src_path.name}")
    print(f"[src] game={src_game} root={src_root_block!r}/{src_root_name!r} "
          f"flags={src_root_flags}  shapes={len(src.shapes)}")

    bones_used = set()
    for s in src.shapes:
        bones_used.update(s.bone_names)
    node_xforms = {name: n.transform for name, n in src.nodes.items()}

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    if dst_path.exists():
        dst_path.unlink()

    dst = NifFile()
    dst.initialize(src_game, str(dst_path),
                   root_type=src_root_block,
                   root_name=src_root_name)

    dst.root.flags = src_root_flags
    try:
        dst.root.write_properties()
    except Exception as e:
        print(f"[warn] root.write_properties failed: {e}")

    fg_src = src.nodes.get("BSFaceGenNiNodeSkinned")
    fg_xform = fg_src.transform if fg_src else identity_xform()
    fg = dst.add_node("BSFaceGenNiNodeSkinned", fg_xform, parent=dst.root)

    for bone in sorted(bones_used):
        xf = node_xforms.get(bone, identity_xform())
        dst.add_node(bone, xf, parent=dst.root)

    for src_shape in src.shapes:
        print(f"[copy] {src_shape.name}")

        # createShapeFromData applies (u, 1-v) on write; pre-unflip.
        src_uvs = [(u, 1.0 - v) for u, v in src_shape.uvs] if src_shape.uvs else []
        # All-zero source normals mean "recompute from geometry"; don't pass.
        src_normals = src_shape.normals
        if src_normals and all(all(abs(c) < 1e-6 for c in n) for n in src_normals):
            src_normals = None

        new_shape = dst.createShapeFromData(
            src_shape.name,
            list(src_shape.verts),
            list(src_shape.tris),
            src_uvs,
            list(src_normals) if src_normals else None,
            use_type=PynBufferTypes.BSDynamicTriShapeBufType,
            parent=fg,
        )

        new_shape.transform = src_shape.transform

        # VERTEX_COLORS shader flag + missing colors = pure-black render.
        if src_shape.colors:
            new_shape.set_colors(list(src_shape.colors))

        new_shape.skin()

        if src_shape.has_global_to_skin:
            new_shape.set_global_to_skin(src_shape.global_to_skin)

        # add_bone resets skin data each call — add all bones first, then
        # set skin-to-bone per bone, then weights.
        src_weights = src_shape.bone_weights
        for bone_name in src_shape.bone_names:
            new_shape.add_bone(bone_name)
        for bone_name in src_shape.bone_names:
            new_shape.set_skin_to_bone_xform(
                bone_name, src_shape.get_shape_skin_to_bone(bone_name))
        for bone_name, vw in src_weights.items():
            new_shape.setShapeWeights(bone_name, vw)

        # Shader: copy the ctypes property buffer wholesale.
        src_sh = src_shape.shader
        src_sh.properties  # lazy-load
        new_sh = new_shape.shader
        if src_sh._properties is not None:
            new_sh._properties = src_sh._properties.copy()
        new_shape.save_shader_attributes()

        for slot, path in src_shape.textures.items():
            if path:
                new_shape.set_texture(slot, path)
        new_shape.save_shader_attributes()

        if src_shape.has_alpha_property:
            new_shape.has_alpha_property = True
            src_alpha = src_shape.alpha_property
            if src_alpha and src_alpha._properties is not None:
                new_shape._alpha._properties = src_alpha._properties.copy()
            new_shape.save_alpha_property()

        # partition_tris returns indices; set_partitions wants IDs.
        src_parts = src_shape.partitions
        src_part_tris = src_shape.partition_tris
        if src_parts:
            part_ids_per_tri = [src_parts[i].id for i in src_part_tris]
            new_shape.set_partitions(src_parts, part_ids_per_tri)

    dst.save()
    print(f"[save] {dst_path} ({os.path.getsize(dst_path)} bytes; "
          f"src was {src_path.stat().st_size} bytes)")
    return dst


if __name__ == "__main__":
    data_root_name = sys.argv[1] if len(sys.argv) > 1 else "Data_vanilla"
    form_id_str = sys.argv[2] if len(sys.argv) > 2 else "0001414D"  # Ulfric
    form_id = int(form_id_str, 16)

    data_root = HERE / data_root_name
    src = reference_facegen_path(data_root, form_id)
    dst = OUT_DIR / data_root_name / f"{form_id:08X}.nif"
    assemble(src, dst)
