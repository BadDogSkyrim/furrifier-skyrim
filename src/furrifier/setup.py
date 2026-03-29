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
    # xEdit enum: 0=Misc, 1=Face, 2=Eyes, 3=Hair, 4=Facial Hair, 5=Scar, 6=Eyebrows
    # Our enum: 0=Hair, 1=Scar, 2=Eyes, 3=Eyebrows, 4=Facial Hair, 5=Unknown
    mapping = {0: HeadpartType.UNKNOWN, 1: HeadpartType.UNKNOWN,
               2: HeadpartType.EYES, 3: HeadpartType.HAIR,
               4: HeadpartType.FACIAL_HAIR, 5: HeadpartType.SCAR,
               6: HeadpartType.EYEBROWS}
    return mapping.get(val, HeadpartType.UNKNOWN)


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
