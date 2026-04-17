"""SOS (Schlongs of Skyrim) compatibility.

Finds SOS addon quests and adds furry races to their compatible race lists.
Ported from BDFurrifySchlongs.pas.

Walks all QUST records in the load order, looking for quests with
SOS_AddonQuest_Script attached. Each such quest has three FormList
properties:
- SOS_Addon_CompatibleRaces
- SOS_Addon_RaceProbabilities
- SOS_Addon_RaceSizes

For each, if a furry race is in the compat list, its furrified vanilla
races are added (with matching probability/size GLOBs). Furrified races
whose furry race is absent are removed.
"""

from __future__ import annotations

import logging
from typing import Optional

from esplib import Plugin, Record
from esplib import flst_add, flst_contains, flst_forms, flst_remove
from esplib import glob_copy_as
from esplib.vmad import VmadData, PROP_OBJECT

from .armor import get_bodypart_flags
from .models import Bodypart
from .util import short_race_name as _short_race_name

log = logging.getLogger(__name__)


def furrify_all_schlongs(plugins,
                         patch: Plugin,
                         race_assignments: dict[str, str],
                         furry_races: dict[str, list[str]],
                         races: dict[str, Record],
                         ) -> int:
    """Add furry races to SOS addon race lists.

    race_assignments: vanilla_edid -> furry_edid
    furry_races: furry_edid -> list of vanilla_edids assigned to it
    races: edid -> Record for all known races

    Returns count of quests modified.
    """
    count = 0

    # Build FormID lookups (normalized to load-order space)
    race_fid_by_edid = {}
    race_edid_by_fid: dict[int, str] = {}
    for edid, rec in races.items():
        norm = rec.normalize_form_id(rec.form_id)
        race_fid_by_edid[edid] = norm
        race_edid_by_fid[norm.value] = edid

    # Build FLST lookup keyed by normalized FormID (not object index —
    # different plugins can have different FLSTs at the same object index)
    flst_by_fid: dict[int, Record] = {}
    for plugin in plugins:
        for rec in plugin.get_records_by_signature('FLST'):
            norm = rec.normalize_form_id(rec.form_id).value
            flst_by_fid[norm] = rec

    # Build GLOB lookup keyed by normalized FormID
    glob_by_fid: dict[int, Record] = {}
    for plugin in plugins:
        for rec in plugin.get_records_by_signature('GLOB'):
            norm = rec.normalize_form_id(rec.form_id).value
            glob_by_fid[norm] = rec

    from esplib.utils import FormID

    for plugin in plugins:
        for quest in plugin.get_records_by_signature('QUST'):
            vmad = VmadData.from_record(quest, 'QUST')
            if vmad is None:
                continue

            sos_script = vmad.get_script('SOS_AddonQuest_Script')
            if sos_script is None:
                continue

            log.info(f"Found SOS quest: {quest.editor_id}")

            # Extract the three FormList references
            compat_prop = sos_script.get_property('SOS_Addon_CompatibleRaces')
            prob_prop = sos_script.get_property('SOS_Addon_RaceProbabilities')
            size_prop = sos_script.get_property('SOS_Addon_RaceSizes')

            if (compat_prop is None or compat_prop.type != PROP_OBJECT
                    or prob_prop is None or prob_prop.type != PROP_OBJECT
                    or size_prop is None or size_prop.type != PROP_OBJECT):
                log.warning(f"SOS quest {quest.editor_id} missing expected properties")
                continue

            # Normalize VMAD FormIDs from quest's plugin space to load-order
            qp = quest.plugin
            compat_fid = qp.normalize_form_id(
                FormID(compat_prop.value.form_id)).value
            prob_fid = qp.normalize_form_id(
                FormID(prob_prop.value.form_id)).value
            size_fid = qp.normalize_form_id(
                FormID(size_prop.value.form_id)).value

            compat_flst = flst_by_fid.get(compat_fid)
            prob_flst = flst_by_fid.get(prob_fid)
            size_flst = flst_by_fid.get(size_fid)

            if compat_flst is None or prob_flst is None or size_flst is None:
                log.warning(f"SOS quest {quest.editor_id}: could not resolve FormLists")
                continue

            modified = _furrify_schlong_lists(
                compat_flst, prob_flst, size_flst,
                patch, race_assignments, furry_races,
                race_fid_by_edid, race_edid_by_fid,
                glob_by_fid,
                quest_edid=quest.editor_id or '',
            )
            if modified:
                count += 1

    log.info(f"Furrified {count} SOS quests")

    arma_count = _furrify_schlong_armas(
        plugins, patch, race_fid_by_edid, race_edid_by_fid, furry_races)
    if arma_count:
        log.info(f"Added subrace support to {arma_count} SOS sheath ARMAs")

    return count


def _furrify_schlong_armas(plugins, patch: Plugin,
                           race_fid_by_edid: dict[str, 'FormID'],
                           race_edid_by_fid: dict[int, str],
                           furry_races: dict[str, list[str]],
                           ) -> int:
    """Add subrace support to SOS sheath ARMAs.

    For each ARMA with the SCHLONG bodypart flag, look at every race it
    already accepts (RNAM + MODL). If any of those is a furry race that
    has vanilla races mapped to it (e.g. Konoi mapped from Reachmen,
    KonoiVampire mapped from ReachmenVampire), add the missing vanilla
    races to the Additional Races list. Without this, the SOS framework
    can equip the sheath ARMO on a subrace NPC but no ARMA matches the
    NPC's race, so the mesh fails to render.
    """
    count = 0
    seen_armas: set[int] = set()

    for plugin in plugins:
        for arma in plugin.get_records_by_signature('ARMA'):
            if not (get_bodypart_flags(arma) & Bodypart.SCHLONG):
                continue

            arma_obj = arma.form_id.value & 0x00FFFFFF
            if arma_obj in seen_armas:
                continue
            seen_armas.add(arma_obj)

            existing: set[int] = set()
            current_edids: list[str] = []
            rnam = arma.get_subrecord('RNAM')
            if rnam and rnam.size >= 4:
                fid = arma.normalize_form_id(rnam.get_form_id()).value
                existing.add(fid)
                edid = race_edid_by_fid.get(fid)
                if edid:
                    current_edids.append(edid)
            for sr in arma.get_subrecords('MODL'):
                if sr.size >= 4:
                    fid = arma.normalize_form_id(sr.get_form_id()).value
                    existing.add(fid)
                    edid = race_edid_by_fid.get(fid)
                    if edid:
                        current_edids.append(edid)

            to_add: list[tuple[str, 'FormID']] = []
            for race_edid in current_edids:
                for vanilla_edid in furry_races.get(race_edid, []):
                    vanilla_fid = race_fid_by_edid.get(vanilla_edid)
                    if vanilla_fid is None or vanilla_fid.value in existing:
                        continue
                    to_add.append((vanilla_edid, vanilla_fid))
                    existing.add(vanilla_fid.value)

            if not to_add:
                continue

            patched = patch.copy_record(arma)
            for vanilla_edid, vanilla_fid in to_add:
                sr = patched.add_subrecord('MODL', b'\x00\x00\x00\x00')
                patch.write_form_id(sr, 0, vanilla_fid)
                log.debug(
                    f"  Added {vanilla_edid} to {arma.editor_id} races")
            count += 1

    return count


def _quest_stem(quest_edid: str) -> str:
    """Derive the GLOB name prefix from a quest editor ID.

    Strips trailing 'Q' or '_Quest' to get the addon name,
    e.g. 'YASDogSheathMaleQ' -> 'YASDogSheathMale'.
    """
    if quest_edid.endswith('_Quest'):
        return quest_edid[:-6]
    if quest_edid.endswith('Q'):
        return quest_edid[:-1]
    return quest_edid


def _furrify_schlong_lists(compat_flst: Record, prob_flst: Record,
                           size_flst: Record, patch: Plugin,
                           race_assignments: dict[str, str],
                           furry_races: dict[str, list[str]],
                           race_fid_by_edid: dict[str, int],
                           race_edid_by_fid: dict[int, str],
                           glob_by_fid: dict[int, Record],
                           quest_edid: str = '',
                           ) -> bool:
    """Process one set of SOS FormLists.

    The three lists must stay strictly parallel: entry N in
    compatibleRaces pairs with entry N in RaceProbabilities and
    RaceSizes. We rebuild all three lists together to guarantee this.

    Returns True if any modifications were made.
    """
    # Read the three parallel lists from the originals
    compat_forms = flst_forms(compat_flst)
    prob_forms = flst_forms(prob_flst)
    size_forms = flst_forms(size_flst)

    # Identify which races are present and which furry races are supported
    # compat_forms are normalized to load-order space by flst_forms
    current_race_edids = set()
    for fid in compat_forms:
        edid = race_edid_by_fid.get(fid.value)
        if edid:
            current_race_edids.add(edid)

    # Determine races to add and remove
    add_races: list[str] = []
    remove_fids: set[int] = set()

    for race_edid in current_race_edids:
        if race_edid in furry_races:
            # Furry race present — add its vanilla races
            for vanilla_edid in furry_races[race_edid]:
                if vanilla_edid not in current_race_edids:
                    add_races.append(vanilla_edid)
        elif race_edid in race_assignments:
            # Vanilla race whose furry race is absent — remove it
            furry_edid = race_assignments[race_edid]
            if furry_edid not in current_race_edids:
                fid = race_fid_by_edid.get(race_edid)
                if fid:
                    remove_fids.add(fid.value)

    if not add_races and not remove_fids:
        return False

    # Build the new parallel entries: (race_fid, prob_fid, size_fid)
    # All FormIDs are in load-order-normalized space (flst_forms
    # normalizes automatically when a PluginSet is available).
    # Start with existing entries, skipping removed races
    entries: list[tuple] = []
    for i, fid in enumerate(compat_forms):
        if fid.value in remove_fids:
            edid = race_edid_by_fid.get(fid.value, '?')
            log.debug(f"  Removed {edid} from SOS lists")
            continue
        prob_fid = prob_forms[i] if i < len(prob_forms) else None
        size_fid = size_forms[i] if i < len(size_forms) else None
        entries.append((fid, prob_fid, size_fid))

    # Add new vanilla races with cloned prob/size GLOBs
    for vanilla_edid in add_races:
        vanilla_fid = race_fid_by_edid.get(vanilla_edid)
        if vanilla_fid is None:
            continue

        # Find the furry race's entry to clone prob/size from
        furry_edid = race_assignments.get(vanilla_edid)
        furry_fid = race_fid_by_edid.get(furry_edid) if furry_edid else None
        furry_index = None
        if furry_fid is not None:
            for i, fid in enumerate(compat_forms):
                if fid.value == furry_fid.value:
                    furry_index = i
                    break

        new_prob_fid = None
        new_size_fid = None
        stem = _quest_stem(quest_edid)
        furry_short = _short_race_name(furry_edid) if furry_edid else ''
        vanilla_short = _short_race_name(vanilla_edid)

        if furry_index is not None:
            if furry_index < len(prob_forms):
                prob_glob = glob_by_fid.get(prob_forms[furry_index].value)
                if prob_glob:
                    new_prob = glob_copy_as(
                        prob_glob,
                        f"{stem}Prob_{furry_short}_{vanilla_short}",
                        patch.get_next_form_id(),
                    )
                    patch.add_record(new_prob)
                    new_prob_fid = new_prob.form_id

            if furry_index < len(size_forms):
                size_glob = glob_by_fid.get(size_forms[furry_index].value)
                if size_glob:
                    new_size = glob_copy_as(
                        size_glob,
                        f"{stem}Size_{furry_short}_{vanilla_short}",
                        patch.get_next_form_id(),
                    )
                    patch.add_record(new_size)
                    new_size_fid = new_size.form_id

        if new_prob_fid is None or new_size_fid is None:
            log.warning(
                f"  Skipping {vanilla_edid} — could not create "
                f"matching prob/size GLOBs")
            continue

        entries.append((vanilla_fid, new_prob_fid, new_size_fid))
        log.info(f"  Added {vanilla_edid} to SOS lists")

    # Write all three lists from the parallel entries
    def _get_or_copy(flst_rec):
        norm_fid = flst_rec.normalize_form_id(flst_rec.form_id)
        patch_fid = patch.denormalize_form_id(norm_fid)
        existing = patch.get_record_by_form_id(patch_fid)
        if existing is not None:
            return existing
        return patch.copy_record(flst_rec)

    compat_patched = _get_or_copy(compat_flst)
    prob_patched = _get_or_copy(prob_flst)
    size_patched = _get_or_copy(size_flst)

    # Clear existing LNAMs
    compat_patched.remove_subrecords('LNAM')
    prob_patched.remove_subrecords('LNAM')
    size_patched.remove_subrecords('LNAM')

    # Write entries in order
    for race_fid, prob_fid, size_fid in entries:
        sr = compat_patched.add_subrecord('LNAM', b'\x00\x00\x00\x00')
        patch.write_form_id(sr, 0, race_fid)

        sr = prob_patched.add_subrecord('LNAM', b'\x00\x00\x00\x00')
        patch.write_form_id(sr, 0, prob_fid)

        sr = size_patched.add_subrecord('LNAM', b'\x00\x00\x00\x00')
        patch.write_form_id(sr, 0, size_fid)

    return True
