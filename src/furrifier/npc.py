"""NPC furrification logic.

Core NPC processing: determine race, replace headparts, apply tint layers.
Ported from BDFurrySkyrim_Furrifier.pas (FurrifyNPC, FurrifyAllNPCs)
and BDFurrySkyrimTools.pas (LoadNPC, SetNpcRaces).
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import Optional

from esplib import Plugin, Record, FormID

from .models import Sex, HeadpartType, HeadpartInfo, RaceAssignment
from .race_defs import RaceDefContext
from .vanilla_setup import unalias
from .furry_load import is_npc_female, is_child_race, get_headpart_type
from .headparts import (
    load_npc_labels, find_similar_headpart,
)
from .tints import RaceTintData, TintChoice, choose_furry_tints

log = logging.getLogger(__name__)


@dataclass
class NPCContext:
    """State for processing a single NPC."""
    record: Record              # The NPC record being patched
    original: Record            # Original NPC record (unpatched)
    sex: Sex
    alias: str                  # Resolved NPC name (via unalias)
    original_race_id: str       # EditorID of the vanilla race
    assigned_race_id: str       # EditorID of the race after faction/forced overrides
    furry_race_id: str          # EditorID of the furry race to use
    labels: list[str] = field(default_factory=list)
    tint_classes: set[str] = field(default_factory=set)


def determine_npc_sex(npc: Record, race: Optional[Record]) -> Sex:
    """Determine the NPC's Sex enum from ACBS flags and race."""
    female = is_npc_female(npc)
    child = is_child_race(race) if race is not None else False
    return Sex.from_flags(female=female, child=child)


def determine_npc_race(npc: Record,
                       ctx: RaceDefContext,
                       races: dict[str, Record],
                       ) -> Optional[tuple[str, str, str]]:
    """Determine vanilla, assigned, and furry race for an NPC.

    Returns (original_race_id, assigned_race_id, furry_race_id) or None
    if the NPC's race isn't furrifiable.
    """
    # Get NPC's race EditorID via RNAM FormID
    rnam = npc.get_subrecord('RNAM')
    if rnam is None:
        return None
    race_fid = rnam.get_uint32()

    # Find race EditorID from FormID
    original_race_id = None
    for edid, rec in races.items():
        if rec.form_id.value == race_fid:
            original_race_id = edid
            break

    if original_race_id is None:
        return None

    # Check if this race is furrifiable
    assigned_race_id = original_race_id

    # Check for forced NPC race
    npc_edid = unalias(npc.editor_id or '')
    if npc_edid in ctx.npc_races:
        assigned_race_id = ctx.npc_races[npc_edid]

    # Check for faction-based race
    # TODO: resolve faction FormIDs and check ctx.faction_races

    # Find the furry race for the assigned race
    if assigned_race_id in ctx.assignments:
        furry_race_id = ctx.assignments[assigned_race_id].furry_id
    elif assigned_race_id in ctx.subraces:
        furry_race_id = ctx.subraces[assigned_race_id].furry_id
    else:
        # Not a furrifiable race
        return None

    return (original_race_id, assigned_race_id, furry_race_id)


def furrify_npc(npc: Record,
                patch: Plugin,
                source_plugin: Plugin,
                ctx: RaceDefContext,
                races: dict[str, Record],
                all_headparts: dict[str, HeadpartInfo],
                race_headparts: dict,
                race_tints: dict[str, RaceTintData],
                max_tint_layers: int = 200,
                ) -> Optional[Record]:
    """Furrify a single NPC.

    Creates an override in the patch plugin with furry race, headparts,
    and tint layers. Returns the patched record, or None if skipped.
    """
    # Skip chargen presets
    acbs = npc['ACBS']
    if acbs and acbs['flags'].Is_CharGen_Face_Preset:
        return None

    # Determine races
    race_result = determine_npc_race(npc, ctx, races)
    if race_result is None:
        return None

    original_race_id, assigned_race_id, furry_race_id = race_result
    race_record = races.get(original_race_id)
    npc_sex = determine_npc_sex(npc, race_record)
    npc_alias = unalias(npc.editor_id or str(npc.form_id))

    log.info(f"Furrifying {npc_alias}: {original_race_id} -> {furry_race_id}")

    # Create override in patch
    patched = patch.copy_record(npc, source_plugin)

    # Only change RNAM for subraces (e.g. Breton -> Reachman).
    # Normal races are furrified at the race record level.
    if assigned_race_id != original_race_id:
        if assigned_race_id in races:
            race_rec = races[assigned_race_id]
            patched.get_subrecord('RNAM').set_uint32(0, race_rec.form_id.value)

    # Remove vanilla character customization
    patched.remove_subrecords('FTST')   # Head texture
    patched.remove_subrecords('QNAM')   # Texture lighting
    patched.remove_subrecords('NAM9')   # Face morph
    patched.remove_subrecords('TINI')   # Tint layers
    patched.remove_subrecords('TINC')
    patched.remove_subrecords('TIAS')
    patched.remove_subrecords('TINV')

    # Load NPC labels for headpart matching
    labels = load_npc_labels(npc, ctx)

    # Replace headparts
    old_headpart_srs = npc.get_subrecords('PNAM')
    patched.remove_subrecords('PNAM')

    for old_sr in old_headpart_srs:
        old_fid = old_sr.get_uint32()
        # Find the old headpart by FormID
        old_hp = None
        for hp in all_headparts.values():
            if hp.record and hp.record.form_id.value == old_fid:
                old_hp = hp
                break
        if old_hp is None:
            continue

        new_hp = find_similar_headpart(
            old_hp, npc_alias, npc_sex, labels,
            furry_race_id, race_headparts, all_headparts, ctx,
        )
        if new_hp and new_hp.record:
            patched.add_subrecord('PNAM', struct.pack('<I', new_hp.record.form_id.value))

    # Apply furry tint layers
    # First, collect what tint classes the vanilla NPC had
    # (already extracted during LoadNPC in Pascal)
    npc_tint_classes: set[str] = set()
    # TODO: extract vanilla NPC tint classes for decoration layer matching

    tint_choices = choose_furry_tints(
        npc_alias, npc_sex, furry_race_id,
        npc_tint_classes, race_tints, max_tint_layers,
    )

    for choice in tint_choices:
        patched.add_subrecord('TINI', struct.pack('<H', choice.tini))
        patched.add_subrecord('TINC', struct.pack('<I', choice.tinc))
        patched.add_subrecord('TIAS', struct.pack('<H', choice.tias))
        patched.add_subrecord('TINV', struct.pack('<f', choice.tinv))

    return patched


def furrify_all_npcs(plugins,
                     patch: Plugin,
                     ctx: RaceDefContext,
                     races: dict[str, Record],
                     all_headparts: dict[str, HeadpartInfo],
                     race_headparts: dict,
                     race_tints: dict[str, RaceTintData],
                     furrify_male: bool = True,
                     furrify_female: bool = True,
                     max_tint_layers: int = 200,
                     ) -> int:
    """Furrify all NPCs across the load order.

    Returns count of NPCs furrified.
    """
    count = 0
    for plugin in plugins:
        npcs = plugin.get_records_by_signature('NPC_')
        for i, npc in enumerate(npcs):
            if (i % 500) == 0 and i > 0:
                log.info(f"  {plugin.file_path.name}: {i}/{len(npcs)}")

            # Skip based on gender filter
            if not furrify_male and not is_npc_female(npc):
                continue
            if not furrify_female and is_npc_female(npc):
                continue

            result = furrify_npc(
                npc, patch, plugin, ctx, races,
                all_headparts, race_headparts, race_tints,
                max_tint_layers,
            )
            if result is not None:
                count += 1

        log.info(f"Processed {plugin.file_path.name}: {len(npcs)} NPCs")

    log.info(f"Total NPCs furrified: {count}")
    return count
