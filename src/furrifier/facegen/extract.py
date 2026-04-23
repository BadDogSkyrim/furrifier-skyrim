"""Build a facegen-engine npc_info dict from a live NPC record.

Port of the extraction logic from `tests/facegen/build_fixtures.stage_npc`
minus the file-copy parts. For live furrifier runs we don't stage
anything into a fixture tree — the AssetResolver handles loose / BSA
lookup at engine time instead.

The returned dict's shape matches one entry in `manifest.json` so
`build_facegen_nif` and `build_facetint_dds` can consume it unchanged.
"""
from __future__ import annotations

import logging
import struct
from typing import Any, Dict, List, Optional

from esplib import PluginSet
from esplib.record import Record


log = logging.getLogger(__name__)


# HDPT.TNAM → TXST texture-slot mapping. Keys are TXST subrecord
# signatures; values are the shader-slot name PyNifly uses. CK's
# facegen bake writes these into the per-NPC nif's shader over
# whatever the source headpart nif carried. TX02/TX05 differ from
# the raw nif-shader slot order — see PLAN_FURRIFIER_FACEGEN.md
# Step 0 findings.
def _norm_slash(path: str) -> str:
    """Manifest storage convention is POSIX (forward slashes) for Data-
    relative paths. The AssetResolver handles either."""
    return path.replace("\\", "/")


_TXST_SLOT_MAP = {
    "TX00": "Diffuse",
    "TX01": "Normal",
    "TX02": "EnvMask",
    "TX03": "SoftLighting",
    "TX04": "HeightMap",
    "TX05": "EnvMap",
    "TX06": "FacegenDetail",
    "TX07": "Specular",
}


def _is_npc_female(npc: Record) -> bool:
    """Female bit is 0x01 of ACBS flags (little-endian uint32)."""
    try:
        return bool(npc["ACBS"]["flags"].Female)
    except Exception:
        acbs = npc.get_subrecord("ACBS")
        if acbs is None or len(acbs.data) < 4:
            return False
        return bool(struct.unpack("<I", acbs.data[:4])[0] & 0x01)


def _read_qnam_color(npc: Record) -> Optional[List[int]]:
    """QNAM: 3 floats (skin-tone RGB in 0-1). Returned as [R, G, B]
    0-255 ints so it can be stored in the npc_info dict unchanged."""
    qnam = npc.get_subrecord("QNAM")
    if qnam is None or qnam.size < 12:
        return None
    r, g, b = struct.unpack("<3f", qnam.data[:12])
    return [max(0, min(255, int(round(v * 255)))) for v in (r, g, b)]


def _read_nam9(npc: Record) -> Optional[List[float]]:
    """NAM9: 19 float32 sliders (slot 0-17 chargen, slot 18 Vampiremorph FLT_MAX sentinel)."""
    nam9 = npc.get_subrecord("NAM9")
    if nam9 is None or nam9.size < 76:
        return None
    return list(struct.unpack("<19f", nam9.data[:76]))


def _read_weight(npc: Record) -> Optional[float]:
    """NAM7: 0-100 float, drives the SkinnyMorph coefficient at facegen time."""
    nam7 = npc.get_subrecord("NAM7")
    if nam7 is None or nam7.size < 4:
        return None
    return struct.unpack("<f", nam7.data[:4])[0]


def _read_nama(npc: Record) -> Optional[List[int]]:
    """NAMA: 4 int32 preset indices (nose, unused, eyes, lips)."""
    nama = npc.get_subrecord("NAMA")
    if nama is None or nama.size < 16:
        return None
    return list(struct.unpack("<4i", nama.data[:16]))


def _headpart_tri_refs(hdpt: Record) -> Dict[str, Optional[str]]:
    """Walk a HDPT's Part subgroups (NAM0/NAM1 pairs) and pull out the
    three tri paths CK cares about:
      type 0 → race_tri  (per-race variant morph)
      type 1 → behavior_tri  (SkinnyMorph + runtime expressions)
      type 2 → chargen_tri  (NAM9 slider morphs)
    Missing types return None. Paths are stored as meshes-relative
    (no leading "meshes\\"); callers prepend it before asking the
    resolver.
    """
    refs: Dict[str, Optional[str]] = {
        "race_tri": None,
        "chargen_tri": None,
        "behavior_tri": None,
    }
    current_type: Optional[int] = None
    for sr in hdpt.subrecords:
        if sr.signature == "NAM0":
            if len(sr.data) >= 4:
                current_type = struct.unpack("<I", sr.data[:4])[0]
            continue
        if sr.signature == "NAM1" and current_type is not None:
            tri_path = sr.data.decode("cp1252", errors="replace").rstrip("\x00")
            if current_type == 0:
                refs["race_tri"] = tri_path
            elif current_type == 1:
                refs["behavior_tri"] = tri_path
            elif current_type == 2:
                refs["chargen_tri"] = tri_path
            current_type = None
    return refs


def _headpart_texture_overrides(
        hdpt: Record, plugin_set: PluginSet) -> Dict[str, str]:
    """Resolve HDPT.TNAM to its TXST record and read slot textures.

    CK's facegen bake writes these over the source nif's shader, which
    is how eye-color / skin-variant HDPTs (EyeDemon,
    SkinEyesFemaleArgonianOlive) land in the per-NPC facegen nif.
    """
    tnam = hdpt.get_subrecord("TNAM")
    if tnam is None:
        return {}
    try:
        txst_fid = tnam.get_form_id()
        txst = plugin_set.resolve_form_id(txst_fid, hdpt.plugin)
    except Exception:
        return {}
    if txst is None:
        return {}
    textures: Dict[str, str] = {}
    for sr in txst.subrecords:
        slot = _TXST_SLOT_MAP.get(sr.signature)
        if slot is None:
            continue
        path = sr.data.decode("cp1252", errors="replace").rstrip("\x00")
        if path:
            textures[slot] = path
    return textures


def _hdpt_type(hdpt: Record) -> Optional[int]:
    """HDPT.PNAM type code: 0 Misc, 1 Face, 2 Eyes, 3 Hair, 4 Facial
    Hair, 5 Scar, 6 Eyebrows."""
    pnam = hdpt.get_subrecord("PNAM")
    if pnam is None or len(pnam.data) < 4:
        return None
    return int.from_bytes(pnam.data[:4], "little")


def _resolve_hdpt_refs(fields: List, plugin_set: PluginSet,
                       source_record: Record) -> List[Record]:
    """Resolve a list of HDPT form-id subrecords to their target records.
    Silently drops unresolvable refs (uncommon)."""
    out: List[Record] = []
    for sr in fields:
        try:
            target = plugin_set.resolve_form_id(sr.get_form_id(), source_record.plugin)
        except Exception:
            target = None
        if target is not None:
            out.append(target)
    return out


def _race_default_headparts(race: Record, is_female: bool,
                            plugin_set: PluginSet) -> List[Record]:
    """Walk the RACE's Head Data section for the given sex and return
    its default HDPT records (one per HEAD subrecord)."""
    target_section = 2 if is_female else 1
    nam0_count = 0
    in_section = False
    heads: List = []
    for sr in race.subrecords:
        if sr.signature == "NAM0":
            nam0_count += 1
            in_section = (nam0_count == target_section)
            continue
        if in_section and sr.signature == "HEAD":
            heads.append(sr)
    return _resolve_hdpt_refs(heads, plugin_set, race)


def _expand_hnam_extras(seeds: List[Record],
                        plugin_set: PluginSet) -> List[Record]:
    """Given a set of HDPT records, return the seeds plus every HDPT
    transitively reachable via HNAM "Extra Parts" references. Order
    preserved; each HDPT appears at most once.

    CK writes a face-hair HDPT's HNAM extras (e.g. HairLineMaleElf09
    for HairMaleElf09) into the facegen nif as sibling shapes, so
    we have to walk them too.
    """
    seen_edids: set[str] = set()
    result: List[Record] = []

    def push(hdpt: Record) -> None:
        edid = hdpt.editor_id
        if edid in seen_edids:
            return
        seen_edids.add(edid)
        result.append(hdpt)

    queue: List[Record] = []
    for s in seeds:
        push(s)
        queue.append(s)

    while queue:
        current = queue.pop(0)
        hnams = [sr for sr in current.subrecords if sr.signature == "HNAM"]
        for extra in _resolve_hdpt_refs(hnams, plugin_set, current):
            if extra.editor_id not in seen_edids:
                push(extra)
                queue.append(extra)
    return result


def _hdpt_entry(hdpt: Record, plugin_set: PluginSet) -> Optional[Dict[str, Any]]:
    """Build one headparts-list entry. Returns None if the HDPT has no
    MODL (no source nif means nothing to include in the facegen)."""
    modl = hdpt.get_subrecord("MODL")
    if modl is None:
        return None
    source_nif = f"meshes/{_norm_slash(modl.get_string())}"

    entry: Dict[str, Any] = {
        "hdpt_edid": hdpt.editor_id,
        "hdpt_type": _hdpt_type(hdpt),
        "source_nif": source_nif,
        "textures": _headpart_texture_overrides(hdpt, plugin_set),
    }
    # Tri paths are stored meshes-relative in the HDPT (like MODL).
    for key, rel in _headpart_tri_refs(hdpt).items():
        entry[key] = f"meshes/{_norm_slash(rel)}" if rel else None
    return entry


def _extract_headparts(
        npc: Record, race: Optional[Record],
        plugin_set: PluginSet, is_female: bool) -> List[Dict[str, Any]]:
    """Resolve the NPC's final headparts set the way CK does at facegen bake:

      1. Start from the race's HEAD defaults (Face, Eyes, Hair, Brows, ...).
      2. For each NPC PNAM, replace the race's default of the same type
         — or add if the NPC has a type not in the race defaults
         (Scar, Facial Hair).
      3. Transitively expand HNAM "Extra Parts" for everything in the
         final set (hair → hairline, beard → 1-bit beard, scar → the
         opposite-side gash, etc.).

    Each produced entry carries source nif, tri paths, and TXST
    texture overrides ready for the assembly pipeline.
    """
    # Step 1: race defaults
    by_type: Dict[int, Record] = {}
    type_none: List[Record] = []  # defaults with no readable type code
    if race is not None:
        for d in _race_default_headparts(race, is_female, plugin_set):
            t = _hdpt_type(d)
            if t is None:
                type_none.append(d)
            else:
                by_type[t] = d
    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "Race %s defaults for %s: types=%s, unresolved=%d",
            race.editor_id if race else "<none>",
            npc.editor_id,
            sorted(by_type.keys()),
            len(type_none))

    # Step 2: NPC PNAMs override / add by type
    pnam_srs = [sr for sr in npc.subrecords if sr.signature == "PNAM"]
    for hdpt in _resolve_hdpt_refs(pnam_srs, plugin_set, npc):
        t = _hdpt_type(hdpt)
        if t is None:
            type_none.append(hdpt)
        else:
            by_type[t] = hdpt

    seeds = list(by_type.values()) + type_none

    # Step 3: HNAM extras, transitively
    all_hdpts = _expand_hnam_extras(seeds, plugin_set)

    results: List[Dict[str, Any]] = []
    for hdpt in all_hdpts:
        entry = _hdpt_entry(hdpt, plugin_set)
        if entry is not None:
            results.append(entry)
    return results


def _extract_npc_tint_entries(npc: Record) -> List[Dict[str, Any]]:
    """NPC's own tint layers — TINI/TINC/TINV/TIAS grouped per layer."""
    out: List[Dict[str, Any]] = []
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


def _extract_race_tint_layers(
        race: Record, is_female: bool) -> List[Dict[str, Any]]:
    """Return race tint layers as an ordered list of dicts in the
    order they appear in the race record.

    Each dict has {tini, mask, tinp}. Male is first NAM0 section,
    female is second. TINP (Tint Mask Type): 6 = Skin Tone,
    7 = War Paint, 14 = Dirt, etc.

    Ordered list (not dict) because tint application order matters
    for the final composite — layers must blend in race-record order
    regardless of the order TINIs happen to appear on individual NPCs.
    """
    target_section = 2 if is_female else 1
    nam0_count = 0
    in_section = False
    layers: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    for sr in race.subrecords:
        if sr.signature == "NAM0":
            nam0_count += 1
            if nam0_count == target_section:
                in_section = True
            elif in_section:
                break
            current = None
            continue

        if not in_section:
            continue

        if sr.signature == "TINI" and len(sr.data) >= 2:
            current = {"tini": struct.unpack("<H", sr.data[:2])[0]}
            layers.append(current)
        elif sr.signature == "TINT" and current is not None:
            current["mask"] = sr.data.decode(
                "cp1252", errors="replace").rstrip("\x00")
        elif sr.signature == "TINP" and current is not None:
            current["tinp"] = struct.unpack("<H", sr.data[:2])[0]

    return layers


def _extract_tints(npc: Record, race: Optional[Record],
                   is_female: bool) -> List[Dict[str, Any]]:
    """Join NPC tint entries (TINI/TINC/TINV) against the race's tint
    table, emitting layers in race-record order.

    Composite blending is order-dependent, and the race record is the
    authoritative ordering — NPCs can carry their TINI subrecords in
    any order (CK normally writes them in race order but nothing
    requires it, and the furrifier's own tint writer doesn't guarantee
    it either). Iterating race-order here keeps the final composite
    consistent regardless of NPC-side ordering.

    Any race layer the NPC doesn't apply (no matching TINI) is skipped.
    Any NPC layer whose race mask can't be resolved is also skipped.
    """
    if race is None:
        return []
    npc_entries_by_tini = {e["tini"]: e for e in _extract_npc_tint_entries(npc)}
    race_layers = _extract_race_tint_layers(race, is_female)
    out: List[Dict[str, Any]] = []
    for race_layer in race_layers:
        npc_entry = npc_entries_by_tini.get(race_layer["tini"])
        if npc_entry is None:
            continue
        mask_path_str = race_layer.get("mask")
        if not mask_path_str:
            continue
        # RACE TINT paths are texture-relative (no leading "textures\\");
        # prepend it so the resolver can look up under Data/.
        mask = f"textures/{_norm_slash(mask_path_str)}"
        out.append({
            **npc_entry,
            "mask": mask,
            "tinp": race_layer.get("tinp"),
        })
    return out


def extract_npc_info(npc: Record, plugin_set: PluginSet,
                     patch_plugin_name: str) -> Dict[str, Any]:
    """Build a facegen-engine npc_info dict from a live NPC record.

    `npc` — the NPC Record, typically an override newly-written into
        the patch.
    `plugin_set` — the full plugin set for HDPT / RACE / TXST lookups.
    `patch_plugin_name` — the filename (e.g. "YASNPCPatch.esp") that
        will own the generated FaceGenData — stamped into the
        FacegenDetail texture path inside the facegen nif.

    The returned dict matches one entry in `manifest.json`. Tri and
    texture paths are stored as Data-relative (backslash-separated,
    starting with "meshes\\" or "textures\\") so the AssetResolver
    can resolve them uniformly.
    """
    form_id_hex = f"{int(npc.form_id):08X}"
    is_female = _is_npc_female(npc)

    # Race
    race: Optional[Record] = None
    race_edid: Optional[str] = None
    rnam = npc.get_subrecord("RNAM")
    if rnam is not None:
        rnam_fid = rnam.get_form_id()
        try:
            race = plugin_set.resolve_form_id(rnam_fid, npc.plugin)
        except Exception as exc:
            log.debug("RNAM resolve raised for %s: %s", npc.editor_id, exc)
            race = None
        if race is None:
            # Dig into WHY it failed — file_index into patch.header.masters,
            # whether that master is in load order, whether the absolute FID
            # has an override chain. One-liner diagnostic.
            masters = npc.plugin.header.masters if npc.plugin else []
            file_idx = rnam_fid.file_index
            master_name = (masters[file_idx]
                           if 0 <= file_idx < len(masters) else "<self>")
            log.debug(
                "RNAM unresolved for %s: raw=%08X file_idx=%d master=%r",
                npc.editor_id, int(rnam_fid), file_idx, master_name)
        else:
            race_edid = race.editor_id

    return {
        "form_id": form_id_hex,
        "base_plugin": patch_plugin_name,
        "npc_edid": npc.editor_id,
        "race_edid": race_edid,
        "is_female": is_female,
        "qnam_color": _read_qnam_color(npc),
        "nam9": _read_nam9(npc),
        "weight": _read_weight(npc),
        "nama": _read_nama(npc),
        "headparts": _extract_headparts(npc, race, plugin_set, is_female),
        "tints": _extract_tints(npc, race, is_female),
    }
