"""Headpart selection and label matching logic.

Ported from BDFurrySkyrimTools.pas (headpart functions) and
BDFurrySkyrim_Furrifier.pas (label scoring, best match selection).
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional

from esplib import Record

from .models import Breed, Sex, HeadpartType, HeadpartInfo
from .race_defs import RaceDefContext
from .util import hash_string

log = logging.getLogger(__name__)


# Eye blindness detection. Headpart EditorIDs encode blindness state in
# their name: plain 'Blind' = fully blind, 'BlindLeft' / 'BlindL' = half
# blind on the left eye, 'BlindRight' / 'BlindR' = half blind on the
# right. The abbreviated forms can appear mid-name in camelCase (e.g.
# 'YASCatDayMaleEyesBlindLAmber'), so the regex ends each form at
# end-of-string, a non-letter, or an uppercase letter.
_BLIND_LEFT_RE = re.compile(r'Blind(Left|L(?=[A-Z]|$|[^a-zA-Z]))')
_BLIND_RIGHT_RE = re.compile(r'Blind(Right|R(?=[A-Z]|$|[^a-zA-Z]))')
_BLIND_FULL_RE = re.compile(r'Blind(?![a-zA-Z])')


def _blindness_state(edid: Optional[str]) -> str:
    """Parse an eye headpart EditorID into its blindness state.

    Returns one of: 'none', 'left', 'right', 'full'.
    """
    if not edid:
        return 'none'
    if _BLIND_LEFT_RE.search(edid):
        return 'left'
    if _BLIND_RIGHT_RE.search(edid):
        return 'right'
    if _BLIND_FULL_RE.search(edid):
        return 'full'
    return 'none'


# Priority tiers for matching a vanilla eye's blindness state to furry
# candidates. Each tier is a set of acceptable states; tiers are tried
# in order until one produces at least one candidate. Half-blind falls
# back to the other side, then sighted (NEVER full blind — per spec).
_BLINDNESS_PRIORITY: dict[str, list[set[str]]] = {
    'none':  [{'none'}],
    'left':  [{'left'}, {'right'}, {'none'}],
    'right': [{'right'}, {'left'}, {'none'}],
    'full':  [{'full'}, {'left', 'right'}, {'none'}],
}


def _filter_by_blindness(candidates: list[HeadpartInfo],
                         target_state: str) -> list[HeadpartInfo]:
    """Return the highest-priority tier of candidates matching target_state."""
    for tier in _BLINDNESS_PRIORITY[target_state]:
        matches = [c for c in candidates
                   if _blindness_state(c.editor_id) in tier]
        if matches:
            return matches
    return []


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


@lru_cache(maxsize=None)
def _score_cached(npc_labels: tuple, hp_labels: tuple,
                  conflicts: frozenset) -> int:
    score = 0
    for npc_label in npc_labels:
        if npc_label in hp_labels:
            score += 1
        else:
            score -= 1
        for hp_label in hp_labels:
            if frozenset({npc_label, hp_label}) in conflicts:
                score = -1000
    return score


def calculate_label_match_score(npc_labels: list[str],
                                hp_labels: list[str],
                                ctx: RaceDefContext) -> int:
    """Score how well a headpart's labels match the NPC's labels.

    Every NPC label that matches a headpart label: +1
    Every NPC label that doesn't match: -1
    Any conflicting labels: -1000
    """
    return _score_cached(
        tuple(npc_labels),
        tuple(hp_labels),
        frozenset(ctx.label_conflicts),
    )


def _breed_whitelist(ctx: RaceDefContext, breed: Optional[Breed],
                     furry_race_id: str, npc_sex: Sex,
                     hp_type: HeadpartType) -> tuple[str, ...]:
    """Resolve the breed-or-race headpart whitelist for this slot.

    Empty tuple = unconstrained pool. Caller intersects with whatever
    candidate set it has.
    """
    sex_name = 'Female' if npc_sex.is_female else 'Male'
    lookup_name = breed.name if breed is not None else furry_race_id
    return ctx.get_headpart_rule(
        lookup_name, sex_name, hp_type.name).headpart_whitelist


def find_best_headpart_match(
    old_hp: HeadpartInfo,
    npc_alias: str,
    npc_sex: Sex,
    npc_labels: list[str],
    furry_race_id: str,
    race_headparts: dict,
    all_headparts: dict[str, HeadpartInfo],
    ctx: RaceDefContext,
    breed: Optional[Breed] = None,
) -> Optional[HeadpartInfo]:
    """Find the best furry headpart to replace a vanilla headpart.

    Uses headpart equivalents (1:1 mappings) if defined, otherwise
    scores all available headparts by label matching. For eye headparts,
    candidates are filtered to match the vanilla eye's blindness state.

    `breed`, when set, constrains the candidate pool to its
    `headpart_whitelist` for this (sex, type) — see PLAN_FURRIFIER_BREEDS.md.
    """
    hp_type = old_hp.hp_type
    sex_key = int(npc_sex)

    # For eyes, constrain candidates by the vanilla eye's blindness state
    # so a sighted NPC can't end up with a blind furry eye (and vice versa).
    target_blind = (_blindness_state(old_hp.editor_id)
                    if hp_type == HeadpartType.EYES else None)

    whitelist = _breed_whitelist(ctx, breed, furry_race_id, npc_sex, hp_type)
    whitelist_set = set(whitelist) if whitelist else None

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
        if target_blind is not None:
            candidates = _filter_by_blindness(candidates, target_blind)
        if whitelist_set is not None:
            candidates = [c for c in candidates
                          if c.editor_id in whitelist_set]
        if candidates:
            # Salt 317: "equivalent-list candidate pick" — independent
            # from the label-match pick below (salt 319).
            idx = hash_string(npc_alias, 317, len(candidates))
            return candidates[idx]

    # No equivalents — find best match by labels
    key = (hp_type, sex_key, furry_race_id)
    if key not in race_headparts:
        log.debug(f"No headparts of type {hp_type.name} for {furry_race_id}/{npc_sex.name}")
        return None

    # race_headparts[key] is a set — iteration order depends on
    # PYTHONHASHSEED, which breaks the hash_string pick downstream
    # (different tie-break orderings → different index). Sort by
    # editor_id so the candidate list is stable across runs.
    available = [all_headparts[hp_id]
                 for hp_id in sorted(race_headparts[key])
                 if hp_id in all_headparts]
    if target_blind is not None:
        available = _filter_by_blindness(available, target_blind)
    if whitelist_set is not None:
        available = [c for c in available if c.editor_id in whitelist_set]
    if not available:
        if whitelist_set is not None:
            log.warning(
                f"breed whitelist {whitelist!r} for {furry_race_id} "
                f"{npc_sex.name} {hp_type.name} matched no headparts "
                f"in the race pool — leaving slot empty for {npc_alias}")
        return None

    best_score = -1000
    best_matches: list[HeadpartInfo] = []

    for hp in available:
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
        # Salt 319: "best-label-match tiebreaker pick". Different from
        # the equivalent-list salt above so an NPC that falls through
        # to this code path doesn't correlate with what the candidate
        # pick would have chosen on the other branch.
        idx = hash_string(npc_alias, 319, len(best_matches))
        return best_matches[idx]

    return None


_PROBABILITY_GATED_TYPES = (HeadpartType.EYEBROWS, HeadpartType.FACIAL_HAIR)


def _should_assign(npc_alias: str, furry_race_id: str, npc_sex: Sex,
                   hp_type: HeadpartType, ctx: RaceDefContext,
                   breed: Optional[Breed] = None) -> bool:
    """Probability gate for EYEBROWS and FACIAL_HAIR. Deterministic
    per (NPC, hp_type). Other types always assign.

    `breed`, when set, looks up breed-specific probability first, with
    fallback to the parent race per the inheritance chain in
    `RaceDefContext.get_headpart_rule`.
    """
    if hp_type not in _PROBABILITY_GATED_TYPES:
        return True
    sex_name = 'Female' if npc_sex.is_female else 'Male'
    lookup_name = breed.name if breed is not None else furry_race_id
    p = ctx.get_headpart_probability(lookup_name, sex_name, hp_type.name)
    if p >= 1.0:
        return True
    if p <= 0.0:
        return False
    # Salt unique per hp_type so eyebrows and facial hair roll independently.
    salt = 491 + int(hp_type)
    return hash_string(npc_alias, salt, 1000) < int(p * 1000)


def find_similar_headpart(
    old_hp: HeadpartInfo,
    npc_alias: str,
    npc_sex: Sex,
    npc_labels: list[str],
    furry_race_id: str,
    race_headparts: dict,
    all_headparts: dict[str, HeadpartInfo],
    ctx: RaceDefContext,
    breed: Optional[Breed] = None,
) -> Optional[HeadpartInfo]:
    """Find a furry equivalent for a vanilla headpart.

    If the old headpart is an "empty" headpart, returns None (skip it).
    Otherwise, adds the old headpart's labels to the NPC's label list
    and finds the best match. `breed`, when set, applies the breed's
    probability gate and headpart whitelist for this (sex, hp_type).
    """
    if old_hp.editor_id in ctx.empty_headparts:
        log.debug(f"Headpart {old_hp.editor_id} is empty, skipping")
        return None

    if not _should_assign(npc_alias, furry_race_id, npc_sex,
                          old_hp.hp_type, ctx, breed=breed):
        log.debug(
            f"Probability skip: {npc_alias} {furry_race_id} "
            f"{old_hp.hp_type.name}")
        return None

    # Add the old headpart's labels to the NPC label context
    working_labels = list(npc_labels)
    if old_hp.editor_id in ctx.headpart_labels:
        for label in ctx.headpart_labels[old_hp.editor_id]:
            add_label_no_conflict(working_labels, label, ctx)

    return find_best_headpart_match(
        old_hp, npc_alias, npc_sex, working_labels,
        furry_race_id, race_headparts, all_headparts, ctx,
        breed=breed,
    )
