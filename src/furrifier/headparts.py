"""Headpart selection and label matching logic.

Ported from BDFurrySkyrimTools.pas (headpart functions) and
BDFurrySkyrim_Furrifier.pas (label scoring, best match selection).
"""

from __future__ import annotations

import logging
from typing import Optional

from esplib import Record

from .models import Sex, HeadpartType, HeadpartInfo
from .race_defs import RaceDefContext
from .util import hash_string

log = logging.getLogger(__name__)


def labels_conflict(label1: str, label2: str, ctx: RaceDefContext) -> bool:
    """Check if two labels conflict."""
    return frozenset({label1, label2}) in ctx.label_conflicts


def add_label_no_conflict(labels: list[str], new_label: str,
                          ctx: RaceDefContext) -> None:
    """Add a label to the list only if it doesn't conflict with existing labels."""
    for existing in labels:
        if labels_conflict(new_label, existing, ctx):
            return
    labels.append(new_label)


def load_npc_labels(npc: Record, ctx: RaceDefContext) -> list[str]:
    """Extract labels describing an NPC from voice type, outfit, and factions.

    Labels like NOBLE, MILITARY, MESSY inform headpart matching.
    """
    labels: list[str] = []

    # Get voice type and outfit as strings for keyword matching
    vtck = npc.get_subrecord('VTCK')
    doft = npc.get_subrecord('DOFT')
    voice = ''
    outfit = ''

    # For linked records we'd need to resolve FormIDs to EditorIDs.
    # For now, use the editor_id of the NPC as context, and apply
    # rules based on faction membership (checked by the caller).
    # The voice/outfit checks use EditorID substring matching in Pascal,
    # which requires resolving the FormID links. We'll implement this
    # when we have PluginSet.resolve_form_id() wired up.

    # TODO: resolve VTCK and DOFT FormIDs to EditorID strings
    # and apply the voice/outfit label rules from Pascal

    return labels


def calculate_label_match_score(npc_labels: list[str],
                                hp_labels: list[str],
                                ctx: RaceDefContext) -> int:
    """Score how well a headpart's labels match the NPC's labels.

    Every NPC label that matches a headpart label: +1
    Every NPC label that doesn't match: -1
    Any conflicting labels: -1000
    """
    score = 0
    for npc_label in npc_labels:
        if npc_label in hp_labels:
            score += 1
        else:
            score -= 1
        for hp_label in hp_labels:
            if labels_conflict(npc_label, hp_label, ctx):
                score = -1000
    return score


def find_best_headpart_match(
    old_hp: HeadpartInfo,
    npc_alias: str,
    npc_sex: Sex,
    npc_labels: list[str],
    furry_race_id: str,
    race_headparts: dict,
    all_headparts: dict[str, HeadpartInfo],
    ctx: RaceDefContext,
) -> Optional[HeadpartInfo]:
    """Find the best furry headpart to replace a vanilla headpart.

    Uses headpart equivalents (1:1 mappings) if defined, otherwise
    scores all available headparts by label matching.
    """
    hp_type = old_hp.hp_type
    sex_key = int(npc_sex)

    # Check for explicit headpart equivalents
    if old_hp.equivalents:
        candidates = []
        for equiv_id in old_hp.equivalents:
            equiv = all_headparts.get(equiv_id)
            if equiv is None:
                continue
            # Verify this headpart works for the furry race
            key = (hp_type, sex_key, furry_race_id)
            if key in race_headparts and equiv_id in race_headparts[key]:
                candidates.append(equiv)
        if candidates:
            idx = hash_string(npc_alias, 317, len(candidates))
            return candidates[idx]

    # No equivalents — find best match by labels
    key = (hp_type, sex_key, furry_race_id)
    if key not in race_headparts:
        log.debug(f"No headparts of type {hp_type.name} for {furry_race_id}/{npc_sex.name}")
        return None

    available = race_headparts[key]  # set of headpart EditorIDs
    best_score = -1000
    best_matches: list[HeadpartInfo] = []

    for hp_id in available:
        hp = all_headparts.get(hp_id)
        if hp is None:
            continue
        hp_labels = hp.labels
        if not hp_labels:
            score = 0
        else:
            score = calculate_label_match_score(npc_labels, hp_labels, ctx)

        if score > best_score:
            best_matches = [hp]
            best_score = score
        elif score == best_score:
            best_matches.append(hp)

    if best_score > -10 and best_matches:
        idx = hash_string(npc_alias, 317, len(best_matches))
        return best_matches[idx]

    return None


def find_similar_headpart(
    old_hp: HeadpartInfo,
    npc_alias: str,
    npc_sex: Sex,
    npc_labels: list[str],
    furry_race_id: str,
    race_headparts: dict,
    all_headparts: dict[str, HeadpartInfo],
    ctx: RaceDefContext,
) -> Optional[HeadpartInfo]:
    """Find a furry equivalent for a vanilla headpart.

    If the old headpart is an "empty" headpart, returns None (skip it).
    Otherwise, adds the old headpart's labels to the NPC's label list
    and finds the best match.
    """
    if old_hp.editor_id in ctx.empty_headparts:
        log.debug(f"Headpart {old_hp.editor_id} is empty, skipping")
        return None

    # Add the old headpart's labels to the NPC label context
    working_labels = list(npc_labels)
    if old_hp.editor_id in ctx.headpart_labels:
        for label in ctx.headpart_labels[old_hp.editor_id]:
            add_label_no_conflict(working_labels, label, ctx)

    return find_best_headpart_match(
        old_hp, npc_alias, npc_sex, working_labels,
        furry_race_id, race_headparts, all_headparts, ctx,
    )
