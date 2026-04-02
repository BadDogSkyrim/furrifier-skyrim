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

    # Build FormID lookups
    race_fid_by_edid = {}
    race_edid_by_obj = {}
    for edid, rec in races.items():
        race_fid_by_edid[edid] = rec.form_id.value
        race_edid_by_obj[rec.form_id.value & 0x00FFFFFF] = edid

    # Build FLST lookup (obj_id -> winning record)
    flst_by_obj: dict[int, Record] = {}
    for plugin in plugins:
        for rec in plugin.get_records_by_signature('FLST'):
            obj_id = rec.form_id.value & 0x00FFFFFF
            flst_by_obj[obj_id] = rec

    # Build GLOB lookup (obj_id -> winning record)
    glob_by_obj: dict[int, Record] = {}
    for plugin in plugins:
        for rec in plugin.get_records_by_signature('GLOB'):
            obj_id = rec.form_id.value & 0x00FFFFFF
            glob_by_obj[obj_id] = rec

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

            compat_flst = flst_by_obj.get(compat_prop.value.form_id & 0x00FFFFFF)
            prob_flst = flst_by_obj.get(prob_prop.value.form_id & 0x00FFFFFF)
            size_flst = flst_by_obj.get(size_prop.value.form_id & 0x00FFFFFF)

            if compat_flst is None or prob_flst is None or size_flst is None:
                log.warning(f"SOS quest {quest.editor_id}: could not resolve FormLists")
                continue

            modified = _furrify_schlong_lists(
                compat_flst, prob_flst, size_flst,
                patch, race_assignments, furry_races,
                race_fid_by_edid, race_edid_by_obj,
                flst_by_obj, glob_by_obj,
            )
            if modified:
                count += 1

    log.info(f"Furrified {count} SOS quests")
    return count


def _furrify_schlong_lists(compat_flst: Record, prob_flst: Record,
                           size_flst: Record, patch: Plugin,
                           race_assignments: dict[str, str],
                           furry_races: dict[str, list[str]],
                           race_fid_by_edid: dict[str, int],
                           race_edid_by_obj: dict[int, str],
                           flst_by_obj: dict[int, Record],
                           glob_by_obj: dict[int, Record],
                           ) -> bool:
    """Process one set of SOS FormLists.

    Returns True if any modifications were made.
    """
    # Get current races in the compat list
    compat_forms = flst_forms(compat_flst)
    current_race_edids = set()
    for fid in compat_forms:
        edid = race_edid_by_obj.get(fid.value & 0x00FFFFFF)
        if edid:
            current_race_edids.add(edid)

    # Determine races to add and remove
    add_races: list[str] = []
    remove_races: list[str] = []

    for race_edid in current_race_edids:
        if race_edid in furry_races:
            # This is a furry race -- add its furrified vanilla races
            for vanilla_edid in furry_races[race_edid]:
                if vanilla_edid not in current_race_edids:
                    add_races.append(vanilla_edid)
        elif race_edid in race_assignments:
            # This is a furrified vanilla race -- check if its furry race
            # is present. If not, remove it.
            furry_edid = race_assignments[race_edid]
            if furry_edid not in current_race_edids:
                remove_races.append(race_edid)

    if not add_races and not remove_races:
        return False

    # Get probability and size lists (parallel to compat list)
    prob_forms = flst_forms(prob_flst)
    size_forms = flst_forms(size_flst)

    # Remove races from all three lists (in reverse order to preserve indices)
    if remove_races:
        compat_patched = patch.copy_record(compat_flst)
        prob_patched = patch.copy_record(prob_flst)
        size_patched = patch.copy_record(size_flst)

        for race_edid in remove_races:
            race_fid = race_fid_by_edid.get(race_edid)
            if race_fid is not None:
                # Find index in compat list
                for i, fid in enumerate(compat_forms):
                    if (fid.value & 0x00FFFFFF) == (race_fid & 0x00FFFFFF):
                        flst_remove(compat_patched, fid.value)
                        if i < len(prob_forms):
                            flst_remove(prob_patched, prob_forms[i].value)
                        if i < len(size_forms):
                            flst_remove(size_patched, size_forms[i].value)
                        break
                log.info(f"  Removed {race_edid} from SOS lists")

    # Add races
    if add_races:
        if not remove_races:
            compat_patched = patch.copy_record(compat_flst)
            prob_patched = patch.copy_record(prob_flst)
            size_patched = patch.copy_record(size_flst)

        for vanilla_edid in add_races:
            vanilla_fid = race_fid_by_edid.get(vanilla_edid)
            if vanilla_fid is None:
                continue

            # Find the furry race's entry to copy prob/size from
            furry_edid = race_assignments.get(vanilla_edid)
            furry_fid = race_fid_by_edid.get(furry_edid) if furry_edid else None
            furry_index = None
            if furry_fid is not None:
                for i, fid in enumerate(compat_forms):
                    if (fid.value & 0x00FFFFFF) == (furry_fid & 0x00FFFFFF):
                        furry_index = i
                        break

            # Add race to compat list
            flst_add(compat_patched, vanilla_fid)

            # Copy prob/size GLOBs from the furry race's entries
            if furry_index is not None:
                if furry_index < len(prob_forms):
                    prob_glob = glob_by_obj.get(
                        prob_forms[furry_index].value & 0x00FFFFFF)
                    if prob_glob:
                        new_prob = glob_copy_as(
                            prob_glob,
                            f"{prob_glob.editor_id}_{vanilla_edid}",
                            patch.get_next_form_id(),
                        )
                        patch._new_records.append(new_prob)
                        flst_add(prob_patched, new_prob.form_id.value)

                if furry_index < len(size_forms):
                    size_glob = glob_by_obj.get(
                        size_forms[furry_index].value & 0x00FFFFFF)
                    if size_glob:
                        new_size = glob_copy_as(
                            size_glob,
                            f"{size_glob.editor_id}_{vanilla_edid}",
                            patch.get_next_form_id(),
                        )
                        patch._new_records.append(new_size)
                        flst_add(size_patched, new_size.form_id.value)

            log.info(f"  Added {vanilla_edid} to SOS lists")

    return True
