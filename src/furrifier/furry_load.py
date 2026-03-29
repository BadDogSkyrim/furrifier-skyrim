"""Setup and data loading from game plugins.

Loads vanilla races, headparts, and tint data from Skyrim plugins using esplib,
then populates RaceInfo objects for furrification.
Ported from BDFurrySkyrimSetup.pas (the plugin-loading parts).
"""

from __future__ import annotations

import logging
from typing import Optional

from esplib import Plugin, Record, PluginSet, LoadOrder

from .models import (
    Sex, HeadpartType, TintLayer, RaceInfo, HeadpartInfo, TintAsset,
)
from .race_defs import RaceDefContext

log = logging.getLogger(__name__)


def is_npc_female(npc: Record) -> bool:
    """Check if an NPC is female from the ACBS flags."""
    acbs = npc.get_subrecord('ACBS')
    if acbs is None:
        return False
    flags = acbs.get_uint32(0)
    return bool(flags & 1)  # Bit 0 = Female


def is_child_race(race: Record) -> bool:
    """Check if a race is a child race from the DATA flags."""
    data = race.get_subrecord('DATA')
    if data is None or data.size < 36:
        return False
    # The child flag is in the race DATA flags at offset 32 (uint32), bit 2
    flags = data.get_uint32(32)
    return bool(flags & 4)


def get_headpart_type(hdpt: Record) -> HeadpartType:
    """Get headpart type from PNAM subrecord."""
    pnam = hdpt.get_subrecord('PNAM')
    if pnam is None:
        return HeadpartType.UNKNOWN
    val = pnam.get_uint32()
    try:
        return HeadpartType(val)
    except ValueError:
        return HeadpartType.UNKNOWN


def load_races(plugin_set: PluginSet, ctx: RaceDefContext) -> dict[str, RaceInfo]:
    """Load all races referenced in the context from the plugin set.

    Returns a dict of EditorID -> RaceInfo for all vanilla and furry races.
    """
    races: dict[str, RaceInfo] = {}

    # Collect all race EditorIDs we need
    needed = set()
    for assignment in ctx.assignments.values():
        needed.add(assignment.vanilla_id)
        needed.add(assignment.furry_id)
    for subrace in ctx.subraces.values():
        needed.add(subrace.vanilla_basis)
        needed.add(subrace.furry_id)

    # Find records in the plugin set
    for plugin in plugin_set:
        if plugin is None:
            continue
        for record in plugin.get_records_by_signature('RACE'):
            edid = record.editor_id
            if edid and edid in needed:
                races[edid] = RaceInfo(  # last wins = winning override
                    record=record,
                    editor_id=edid,
                    is_child=is_child_race(record),
                )

    log.info(f"Loaded {len(races)} race records")

    # Link assignments to their RaceInfo
    for assignment in ctx.assignments.values():
        assignment.vanilla = races.get(assignment.vanilla_id)
        assignment.furry = races.get(assignment.furry_id)
        if assignment.vanilla is None:
            log.warning(f"Vanilla race not found: {assignment.vanilla_id}")
        if assignment.furry is None:
            log.warning(f"Furry race not found: {assignment.furry_id}")

    return races


def load_headparts(plugin_set: PluginSet,
                   ctx: RaceDefContext) -> dict[str, HeadpartInfo]:
    """Load all HDPT records and attach labels from the context."""
    headparts: dict[str, HeadpartInfo] = {}

    for plugin in plugin_set:
        if plugin is None:
            continue
        for record in plugin.get_records_by_signature('HDPT'):
            edid = record.editor_id
            if edid is None:
                continue
            hp_type = get_headpart_type(record)
            labels = ctx.headpart_labels.get(edid, [])
            equivalents = ctx.headpart_equivalents.get(edid, [])
            headparts[edid] = HeadpartInfo(
                record=record,
                editor_id=edid,
                hp_type=hp_type,
                labels=list(labels),
                equivalents=list(equivalents),
            )

    log.info(f"Loaded {len(headparts)} headpart records")
    return headparts


def build_race_headparts(plugins: list[Plugin],
                         all_headparts: dict[str, HeadpartInfo],
                         ) -> dict[tuple, set[str]]:
    """Build an index of headparts available per (type, sex, race).

    Returns a dict mapping (HeadpartType, sex_int, race_edid) to a set
    of headpart EditorIDs.

    Each HDPT record has:
    - PNAM: headpart type
    - DATA: flags byte (bit 0 = male, bit 2 = female)
    - RNAM: FormID → FormList (FLST) of valid races
    """
    # Build a FormID→EditorID lookup for all RACE records across plugins
    race_fid_to_edid: dict[int, str] = {}
    for plugin in plugins:
        if plugin is None:
            continue
        local_idx = len(plugin.header.masters)
        for record in plugin.get_records_by_signature('RACE'):
            edid = record.editor_id
            if edid:
                # Store with the object ID only (master-independent)
                obj_id = record.form_id.value & 0x00FFFFFF
                race_fid_to_edid[obj_id] = edid

    # Build a FormID→FLST record lookup for FormList resolution
    flst_by_fid: dict[int, Record] = {}
    for plugin in plugins:
        if plugin is None:
            continue
        for record in plugin.get_records_by_signature('FLST'):
            obj_id = record.form_id.value & 0x00FFFFFF
            flst_by_fid[obj_id] = record

    race_headparts: dict[tuple, set[str]] = {}

    for hp in all_headparts.values():
        if hp.record is None:
            continue

        # Get DATA flags for sex filtering
        data_sr = hp.record.get_subrecord('DATA')
        if data_sr is None or data_sr.size < 1:
            continue
        flags = data_sr.data[0]
        is_male = bool(flags & 0x02)    # bit 1
        is_female = bool(flags & 0x04)  # bit 2

        # Get RNAM → FormList
        rnam = hp.record.get_subrecord('RNAM')
        if rnam is None:
            continue
        rnam_fid = rnam.get_uint32()
        rnam_obj = rnam_fid & 0x00FFFFFF

        flst = flst_by_fid.get(rnam_obj)
        if flst is None:
            continue

        # Get races from the FormList's LNAM entries
        race_edids = set()
        for lnam in flst.get_subrecords('LNAM'):
            lnam_obj = lnam.get_uint32() & 0x00FFFFFF
            edid = race_fid_to_edid.get(lnam_obj)
            if edid:
                race_edids.add(edid)

        # Insert into index for each applicable sex and race
        sexes = []
        if is_male:
            sexes.extend([Sex.MALE_ADULT, Sex.MALE_CHILD])
        if is_female:
            sexes.extend([Sex.FEMALE_ADULT, Sex.FEMALE_CHILD])

        for sex in sexes:
            for race_edid in race_edids:
                key = (hp.hp_type, sex, race_edid)
                if key not in race_headparts:
                    race_headparts[key] = set()
                race_headparts[key].add(hp.editor_id)

    log.info(f"Built race_headparts index: {len(race_headparts)} entries")
    return race_headparts
