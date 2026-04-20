"""
Fixture builder for the facegen engine tests.

For each test NPC:
  - Find the reference CK-built facegen.nif at `.../FaceGeom/<base-plugin>/<formid>.nif`
    in the appropriate source tree (vanilla-assets vs game folder).
  - Read that nif's shape names — these are exactly the HDPT EditorIDs CK used.
  - For each shape name, look up the HDPT record by EditorID, read its MODL
    (nif path relative to Data\\meshes\\), and copy that nif from the source
    tree into the test tree under the same relative path.
  - Also copy the per-NPC FaceTint dds if present.

After one run, tests are independent of the ESMs and the game folder.
"""
import json
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"c:\Modding\xEditDev\esplib\src")))
sys.path.insert(0, r"C:\Modding\PyNifly\io_scene_nifly")

from esplib import LoadOrder, PluginSet
from pyn.pynifly import NifFile

GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")
VANILLA_ASSETS = Path(r"C:\Modding\SkyrimSEAssets\00 Vanilla Assets")

TESTS_ROOT = Path(__file__).parent
DATA_VANILLA = TESTS_ROOT / "Data_vanilla"
DATA_FURRY = TESTS_ROOT / "Data_furry"


VANILLA_PICKS = [
    (0x00013268, "argonian_female"),   # Deeja — pure vanilla, beast race
    (0x0001327C, "woodelf_male"),      # Dervenin — 10 shapes, most complex
    (0x0001414D, "nord_male_ulfric"),  # Ulfric — beard + scar; also a furry pair
]

FURRY_PICKS = [
    (0x00013255, "lykaios_male"),      # Addvar — NordRace furrified to Lykaios
    (0x0001414D, "nord_male_ulfric"),  # Ulfric — vanilla-furry pair with above
]


# ---------------------------------------------------------------- helpers --

def plugin_filename(plugin):
    return plugin.file_path.name


def is_npc_female(npc) -> bool:
    """Female is bit 0 of ACBS flags (0x01). Use esplib's FlagSet decoder
    if available for robustness."""
    try:
        return bool(npc["ACBS"]["flags"].Female)
    except Exception:
        acbs = npc.get_subrecord("ACBS")
        if acbs is None or len(acbs.data) < 4:
            return False
        return bool(struct.unpack("<I", acbs.data[:4])[0] & 0x01)


def extract_npc_tint_layers(npc) -> list[dict]:
    """Read the NPC's TINI/TINC/TINV/TIAS grouped subrecord sequences.

    Returns a list of {tini, color (RGBA tuple), intensity (float 0-1), tias}
    for each layer with intensity > 0. TINV is stored as int(intensity*100).
    """
    out = []
    subs = list(npc.subrecords)
    for i, sr in enumerate(subs):
        if sr.signature != "TINI":
            continue
        tini = struct.unpack("<H", sr.data[:2])[0]
        color = None
        tinv = 0
        tias = -1
        for j in range(i + 1, min(i + 4, len(subs))):
            nxt = subs[j]
            if nxt.signature == "TINC" and len(nxt.data) >= 4:
                color = tuple(nxt.data[:4])
            elif nxt.signature == "TINV" and len(nxt.data) >= 4:
                tinv = struct.unpack("<I", nxt.data[:4])[0]
            elif nxt.signature == "TIAS" and len(nxt.data) >= 2:
                tias = struct.unpack("<h", nxt.data[:2])[0]
            elif nxt.signature == "TINI":
                break
        if tinv > 0 and color is not None:
            out.append({
                "tini": tini,
                "color": list(color),
                "intensity": tinv / 100.0,
                "tias": tias,
            })
    return out


def extract_race_tint_layers(race, is_female: bool) -> dict[int, dict]:
    """Return {tini_index: {"mask": path, "tinp": type_code}} for the given
    sex's Head Data. TINP (Tint Mask Type) identifies the semantic role of
    each layer: 6 = Skin Tone, 7 = War Paint, 14 = Dirt, etc.

    Male = first NAM0 section, female = second.
    """
    subs = list(race.subrecords)
    target_section = 2 if is_female else 1
    nam0_count = 0
    in_section = False
    layers: dict[int, dict] = {}
    current_tini = None

    for sr in subs:
        if sr.signature == "NAM0":
            nam0_count += 1
            if nam0_count == target_section:
                in_section = True
            elif in_section:
                break
            current_tini = None
            continue

        if not in_section:
            continue

        if sr.signature == "TINI" and len(sr.data) >= 2:
            current_tini = struct.unpack("<H", sr.data[:2])[0]
            layers.setdefault(current_tini, {})
        elif sr.signature == "TINT" and current_tini is not None:
            layers[current_tini]["mask"] = sr.data.decode(
                "cp1252", errors="replace").rstrip("\x00")
        elif sr.signature == "TINP" and current_tini is not None:
            layers[current_tini]["tinp"] = struct.unpack("<H", sr.data[:2])[0]

    return layers


def find_case_insensitive(path: Path) -> Path | None:
    """Return the file matching `path` with case-insensitive name lookup,
    or None if nothing matches. Skyrim pathing is inconsistent."""
    if path.is_file():
        return path
    parent = path.parent
    if not parent.is_dir():
        return None
    target = path.name.lower()
    for p in parent.iterdir():
        if p.name.lower() == target:
            return p
    return None


def copy_file(src: Path, dst: Path, label: str) -> bool:
    resolved = find_case_insensitive(src)
    if resolved is None:
        print(f"    [MISS] {label}: {src}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(resolved, dst)
    return True


def stage_npc(plugin_set, form_id, label, source_data_dir, dest_tree):
    """Stage one NPC. Returns a manifest entry dict or None on failure."""
    print(f"\n=== {label} (0x{form_id:08X}) from {source_data_dir.name} ===")

    chain = plugin_set.get_override_chain(form_id)
    if not chain:
        print("  [MISS] no override chain")
        return None
    base_plugin = chain[0].plugin
    base_name = plugin_filename(base_plugin)
    print(f"  NPC EDID = {chain[-1].editor_id}  base = {base_name}")

    # Reference facegen nif
    fg_rel = Path("meshes") / "actors" / "character" / "FaceGenData" / "FaceGeom" / \
             base_name / f"{form_id:08X}.nif"
    fg_src = source_data_dir / fg_rel
    fg_dst = dest_tree / fg_rel
    if not copy_file(fg_src, fg_dst, "facegen.nif"):
        return None
    print(f"  [copy] {fg_rel}")

    # Facetint dds (optional — copy for later tint-merge work)
    tint_rel = Path("textures") / "actors" / "character" / "FaceGenData" / "FaceTint" / \
               base_name / f"{form_id:08X}.dds"
    tint_copied = copy_file(source_data_dir / tint_rel, dest_tree / tint_rel, "facetint.dds")
    if tint_copied:
        print(f"  [copy] {tint_rel}")

    # Read shape names — these are the HDPT editor IDs CK used
    ref_nif = NifFile(str(find_case_insensitive(fg_src)))
    shape_names = [s.name for s in ref_nif.shapes]
    print(f"  {len(shape_names)} shapes in facegen")

    headparts = []
    for edid in shape_names:
        hdpt = plugin_set.get_record_by_edid("HDPT", edid)
        if hdpt is None:
            print(f"    [miss HDPT] {edid}")
            continue
        modl = hdpt.get_subrecord("MODL")
        if modl is None:
            print(f"    [no MODL] {edid}")
            continue
        model_rel_str = modl.get_string()
        rel_path = Path("meshes") / Path(model_rel_str)
        # HDPT type (PNAM): 0 Misc, 1 Face, 2 Eyes, 3 Hair, 4 Facial Hair,
        # 5 Scar, 6 Eyebrows. Only Face gets the per-NPC FacegenDetail stamp.
        pnam = hdpt.get_subrecord("PNAM")
        hdpt_type = (int.from_bytes(pnam.data[:4], "little")
                     if pnam and len(pnam.data) >= 4 else None)
        # Walk the HDPT Part subgroups to find tri file paths:
        #   NAM0 type 0 = Races tri (per-race variant morph)
        #   NAM0 type 1 = behavior tri (runtime expressions +
        #                 SkinnyMorph, which CK bakes per weight)
        #   NAM0 type 2 = chargen tri (NAM9-driven morphs)
        race_tri = None
        chargen_tri = None
        behavior_tri = None
        current_type = None
        for sr in hdpt.subrecords:
            if sr.signature == "NAM0":
                current_type = struct.unpack("<I", sr.data[:4])[0]
            elif sr.signature == "NAM1" and current_type is not None:
                tri_path = sr.data.decode("cp1252", errors="replace").rstrip("\x00")
                if current_type == 0:
                    race_tri = tri_path
                elif current_type == 1:
                    behavior_tri = tri_path
                elif current_type == 2:
                    chargen_tri = tri_path
                current_type = None

        # HDPT.TNAM → TXST → texture paths. CK overrides the source
        # headpart nif's shader with these, which is how eye-color /
        # skin-variant HDPTs (EyeDemon, SkinEyesFemaleArgonianOlive)
        # land in the facegen nif. TXST slot → nif shader slot name:
        #   TX00→Diffuse, TX01→Normal, TX02→EnvMask, TX03→SoftLighting,
        #   TX04→HeightMap, TX05→EnvMap, TX06→FacegenDetail, TX07→Specular.
        # TX02/TX05 are "swapped" relative to raw shader indices — the
        # TXST record's column order differs from NIF texture-slot order.
        textures: dict[str, str] = {}
        tnam = hdpt.get_subrecord("TNAM")
        if tnam is not None:
            try:
                txst_fid = tnam.get_form_id()
                txst = plugin_set.resolve_form_id(txst_fid, hdpt.plugin)
            except Exception as e:
                print(f"    [warn] {edid} TXST resolve failed: {e}")
                txst = None
            if txst is not None:
                slot_map = {
                    "TX00": "Diffuse",  "TX01": "Normal",
                    "TX02": "EnvMask",  "TX03": "SoftLighting",
                    "TX04": "HeightMap","TX05": "EnvMap",
                    "TX06": "FacegenDetail", "TX07": "Specular",
                }
                for sr in txst.subrecords:
                    sig = sr.signature
                    if sig in slot_map:
                        path = sr.data.decode("cp1252", errors="replace").rstrip("\x00")
                        if path:
                            textures[slot_map[sig]] = path
        if copy_file(source_data_dir / rel_path, dest_tree / rel_path, f"headpart {edid}"):
            print(f"    [copy] {edid}: {model_rel_str}")
            # Copy tri files; record relpath only if copy succeeded.
            # A missing tri on disk is different from "no tri referenced":
            # if referenced but not on disk, record the relpath anyway so
            # the morph pipeline can warn-and-skip consistently.
            race_tri_rel = None
            chargen_tri_rel = None
            behavior_tri_rel = None
            if race_tri:
                rel = Path("meshes") / Path(race_tri.replace("\\", "/"))
                copy_file(source_data_dir / rel, dest_tree / rel, f"  race_tri {edid}")
                race_tri_rel = rel.as_posix()
            if chargen_tri:
                rel = Path("meshes") / Path(chargen_tri.replace("\\", "/"))
                copy_file(source_data_dir / rel, dest_tree / rel, f"  chargen_tri {edid}")
                chargen_tri_rel = rel.as_posix()
            if behavior_tri:
                rel = Path("meshes") / Path(behavior_tri.replace("\\", "/"))
                copy_file(source_data_dir / rel, dest_tree / rel, f"  behavior_tri {edid}")
                behavior_tri_rel = rel.as_posix()
            headparts.append({
                "hdpt_edid": edid,
                "hdpt_type": hdpt_type,
                "source_nif": rel_path.as_posix(),
                "race_tri": race_tri_rel,
                "chargen_tri": chargen_tri_rel,
                "behavior_tri": behavior_tri_rel,
                "textures": textures,
            })

    # Tint layers — read NPC's TINI/TINC/TINV/TIAS, resolve each to a mask
    # file via the RACE's TINI table, copy the mask into the fixture.
    npc = chain[-1]
    rnam = npc.get_subrecord("RNAM")
    race = None
    tint_entries = []

    # QNAM is the NPC's base skin color (3 floats RGB, 0-1 range).
    # CK applies this as the solid base of the face tint before layering
    # on per-layer TINC overlays. Required for NPCs that don't have an
    # explicit SkinTone TINI entry (Dervenin, Deeja, etc.).
    qnam_color = None
    qnam = npc.get_subrecord("QNAM")
    if qnam is not None and qnam.size >= 12:
        r, g, b = struct.unpack("<3f", qnam.data[:12])
        qnam_color = [max(0, min(255, int(round(v * 255)))) for v in (r, g, b)]

    # NAM9: 19 floats, slots 0-17 are chargen slider values in [-1, +1],
    # slot 18 is Vampiremorph (FLT_MAX = "not set" for non-vampires).
    nam9_floats = None
    nam9 = npc.get_subrecord("NAM9")
    if nam9 is not None and nam9.size >= 76:
        nam9_floats = list(struct.unpack("<19f", nam9.data[:76]))

    # NAM7 weight: 0-100 float. CK bakes SkinnyMorph at facegen time
    # with coefficient (100 - weight) / 100.
    weight = None
    nam7 = npc.get_subrecord("NAM7")
    if nam7 is not None and nam7.size >= 4:
        weight = struct.unpack("<f", nam7.data[:4])[0]

    # NAMA: 4 int32 preset indices (nose, unknown, eyes, lips). Each
    # non-(-1) index N selects `{Nose,Eyes,Lip}Type{N}` from the chargen
    # tri and applies it at coefficient 1.0.
    nama = None
    nama_sr = npc.get_subrecord("NAMA")
    if nama_sr is not None and nama_sr.size >= 16:
        nama = list(struct.unpack("<4i", nama_sr.data[:16]))
    if rnam is not None:
        race = plugin_set.resolve_form_id(rnam.get_form_id(), npc.plugin)
    if race is not None:
        race_layers = extract_race_tint_layers(race, is_npc_female(npc))
        tint_layers = extract_npc_tint_layers(npc)
        print(f"  {len(tint_layers)} tint layers (non-zero intensity); "
              f"race has {len(race_layers)} layer entries")
        for layer in tint_layers:
            race_layer = race_layers.get(layer["tini"])
            mask_path_str = race_layer.get("mask") if race_layer else None
            if not mask_path_str:
                print(f"    [miss TINT] tini={layer['tini']}")
                continue
            tinp = race_layer.get("tinp") if race_layer else None
            # RACE TINT paths are relative to Data\textures\ (no leading
            # "textures\" segment), unlike HDPT MODL paths which are relative
            # to Data\meshes\. Prepend the right folder.
            mask_rel = Path("textures") / Path(mask_path_str.replace("\\", "/"))
            src_mask = source_data_dir / mask_rel
            dst_mask = dest_tree / mask_rel
            if copy_file(src_mask, dst_mask, f"tint mask tini={layer['tini']}"):
                print(f"    [copy] tini={layer['tini']:2d} TINP={tinp} "
                      f"c={tuple(layer['color'])} v={layer['intensity']:.2f} "
                      f"tias={layer['tias']}  {mask_path_str}")
                tint_entries.append({
                    **layer,
                    "mask": mask_rel.as_posix(),
                    "tinp": tinp,
                })

    return {
        "form_id": f"{form_id:08X}",
        "label": label,
        "base_plugin": base_name,
        "npc_edid": chain[-1].editor_id,
        "race_edid": race.editor_id if race is not None else None,
        "is_female": is_npc_female(npc),
        "facegen_nif": fg_rel.as_posix(),
        "facetint_dds": tint_rel.as_posix() if tint_copied else None,
        "qnam_color": qnam_color,  # [R, G, B] 0-255, NPC's base skin tone
        "nam9": nam9_floats,       # 19 floats: slots 0-17 sliders, slot 18 vampire
        "weight": weight,          # 0-100 float; drives SkinnyMorph coefficient
        "nama": nama,              # 4 int32 preset indices: (nose, ?, eyes, lips)
        "headparts": headparts,
        "tints": tint_entries,
    }


def write_manifest(dest_tree: Path, entries: list) -> None:
    manifest_path = dest_tree / "manifest.json"
    manifest = {"npcs": [e for e in entries if e is not None]}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\n[manifest] {manifest_path}  ({len(manifest['npcs'])} NPCs)")


def build_vanilla():
    print("\n############# VANILLA #############")
    load_order = LoadOrder.from_list(
        ["Skyrim.esm"],
        data_dir=str(GAME_DATA),
        game_id="tes5",
    )
    ps = PluginSet(load_order)
    ps.load_all()
    entries = [stage_npc(ps, fid, label, VANILLA_ASSETS, DATA_VANILLA)
               for fid, label in VANILLA_PICKS]
    write_manifest(DATA_VANILLA, entries)


def build_furry():
    print("\n############# FURRY #############")
    # Load YASNPCPatchUng.esp + its full master chain so HDPT records from
    # YASCanineRaces / YASFurryWorld / etc. are all resolvable.
    ps = PluginSet.from_plugin(
        str(GAME_DATA / "YASNPCPatchUng.esp"),
        game_id="tes5",
    )
    entries = [stage_npc(ps, fid, label, GAME_DATA, DATA_FURRY)
               for fid, label in FURRY_PICKS]
    write_manifest(DATA_FURRY, entries)


if __name__ == "__main__":
    build_vanilla()
    build_furry()
    print("\nDone.")
