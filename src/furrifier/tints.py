"""Tint layer logic for NPC furrification.

Selects furry tint layers (skin tone, fur patterns, markings) for NPCs
based on the furry race's available tint assets.
Ported from BDFurrySkyrim_Furrifier.pas tint functions.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from .models import BreedTintRule, Sex, TintAsset, TintLayer
from .util import hash_string

log = logging.getLogger(__name__)

# Tint layer class names in order matching TintLayer enum
TINT_CLASS_NAMES = [
    'Skin Tone', 'Cheek Color Lower', 'Chin', 'EyeSocket Lower',
    'EyeSocket Upper', 'Eyeliner', 'Forehead', 'Laugh Lines',
    'Lip Color', 'Neck', 'Nose', 'Cheek Color', 'Muzzle',
    'Mustache', 'Stripes', 'Spots', 'Mask', 'Brow', 'Ear',
    'BlackBlood', 'Bothiah', 'Forsworn', 'Frekles',
    'NordWarPaint', 'DarkElfWarPaint', 'ImperialWarPaint',
    'OrcWarPaint', 'RedguardWarPaint', 'WoodElfWarPaint',
    'Wolfpawprint', 'Skull', 'Paint', 'Dirt',
]


def class_name_to_layer(name: str) -> int:
    """Convert tint class name to TintLayer index."""
    try:
        return TINT_CLASS_NAMES.index(name)
    except ValueError:
        return -1


@dataclass
class RaceTintData:
    """Pre-indexed tint assets for a race/sex combination.

    Organized by tint class name -> list of TintAsset.
    """
    classes: dict[str, list[TintAsset]] = field(default_factory=dict)
    required: set[str] = field(default_factory=set)


@dataclass
class TintChoice:
    """A single tint layer to apply to an NPC."""
    tini: int       # Tint index
    tinc: int       # Color FormID
    tinv: float     # Intensity value
    tias: int       # Preset index


def choose_tint_preset(npc_alias: str, seed: int, presets: list,
                       skip_first: bool = False) -> Optional[int]:
    """Choose a preset index from a list using deterministic hash.

    skip_first: if True, skip the first preset (used for non-skin-tone layers
    where the first preset is typically the "default/none" option).
    """
    if not presets:
        return None

    lo = 1 if skip_first else 0
    hi = len(presets) - 1
    if skip_first:
        hi -= 1

    if hi < 0:
        return 0

    idx = hash_string(npc_alias, seed, hi + 1) + lo
    if idx >= len(presets):
        idx = len(presets) - 1

    return idx


def choose_furry_tints(npc_alias: str, npc_sex: Sex,
                       furry_race_id: str,
                       npc_tint_classes: set[str],
                       race_tints: dict[str, RaceTintData],
                       max_layers: int = 200) -> list[TintChoice]:
    """Choose furry tint layers for an NPC.

    Args:
        npc_alias: NPC identifier for deterministic hash-based selection
        npc_sex: NPC sex
        furry_race_id: EditorID of the furry race
        npc_tint_classes: set of tint class names the vanilla NPC already has
        race_tints: pre-indexed tint data by race ID
        max_layers: maximum fur layers to apply

    Returns list of TintChoice to apply to the NPC.
    """
    key = (furry_race_id, npc_sex)
    if key not in race_tints:
        log.warning(f"No tint data for race {furry_race_id}")
        return []

    data = race_tints[key]
    choices: list[TintChoice] = []

    # 1. Skin tone (always applied, don't skip first preset)
    if 'Skin Tone' in data.classes and data.classes['Skin Tone']:
        skin_assets = data.classes['Skin Tone']
        asset = skin_assets[0]  # Use first skin tone asset
        if asset.presets:
            idx = choose_tint_preset(npc_alias, 1455, asset.presets, skip_first=False)
            if idx is not None:
                preset = asset.presets[idx]
                choices.append(TintChoice(
                    tini=asset.index,
                    tinc=preset[0],  # color FormID
                    tinv=preset[1],  # intensity
                    tias=preset[2],  # TIRS preset index
                ))

    # 2. Fur layers and decoration layers in pseudo-random order
    # Build randomized index list (same as Pascal RandomizeIndexList)
    class_names = list(data.classes.keys())
    randomized = _randomize_index_list(npc_alias, 5345, len(class_names))

    fur_count = 0
    for order_idx in randomized:
        if order_idx >= len(class_names):
            continue
        class_name = class_names[order_idx]
        layer_id = class_name_to_layer(class_name)
        is_required = class_name in data.required

        if layer_id == TintLayer.SKIN_TONE:
            continue  # Already handled skin tone above

        if layer_id < 0:
            continue  # Unknown class (e.g. 'Old') — skip for now

        # Determine if we should apply this layer
        should_apply = False
        if layer_id < TintLayer.DECORATION_LO and fur_count < max_layers:
            should_apply = True
        elif layer_id >= TintLayer.DECORATION_LO and class_name in npc_tint_classes:
            should_apply = True
        elif is_required:
            should_apply = True

        if not should_apply:
            continue

        assets = data.classes[class_name]
        if not assets:
            continue

        # Choose which asset variant to use
        asset_idx = hash_string(npc_alias, 529, len(assets)) if len(assets) > 1 else 0
        asset = assets[asset_idx]

        if asset.presets:
            preset_idx = choose_tint_preset(npc_alias, 1455, asset.presets, skip_first=True)
            if preset_idx is not None:
                preset = asset.presets[preset_idx]
                choices.append(TintChoice(
                    tini=asset.index,
                    tinc=preset[0],
                    tinv=preset[1],
                    tias=preset[2],  # TIRS preset index
                ))

        if layer_id < TintLayer.DECORATION_LO:
            fur_count += 1

    return choices


def choose_breed_tints(
        npc_alias: str,
        rules: list[BreedTintRule],
        race_data: 'RaceTintData',
        form_id_for_edid: Callable[[str], Optional[int]],
) -> list[TintChoice]:
    """Emit tint choices for a breed-tagged NPC per its breed rules.

    Algorithm (Phase 3 of PLAN_FURRIFIER_BREEDS.md):
    1. For each rule, find every parent-race TINI whose filename
       contains the rule's mask substring (decision #6).
    2. Filter the rule's color EDIDs to those that resolve to a CLFM
       whose form-id is among the matched TINI's preset color form-ids
       (decision #7); drop missing entries with a warning.
    3. Hash-roll the rule's probability; if not applied, skip the
       layer entirely.
    4. If applied: hash-pick one resolved color, emit one TintChoice
       per matched TINI using the parent's TINI/TINP/TIAS metadata.

    `form_id_for_edid` resolves a CLFM EditorID to its load-order
    form-id (FurryContext-supplied; lazy index).
    """
    choices: list[TintChoice] = []
    if not rules:
        return choices
    # Flatten parent's tint assets across all classes for substring match.
    all_assets: list[TintAsset] = []
    for assets in race_data.classes.values():
        all_assets.extend(assets)
    for rule in rules:
        matches = [a for a in all_assets if rule.mask_substring in a.filename]
        if not matches:
            log.warning(
                f"breed tint rule mask {rule.mask_substring!r} matched no "
                f"parent TINI; dropping rule")
            continue
        # Probability gate per rule (independent of mask multiplicity).
        salt = 7411 + (sum(ord(c) for c in rule.mask_substring) % 997)
        if rule.probability < 1.0:
            if rule.probability <= 0.0:
                continue
            if hash_string(npc_alias, salt, 1000) >= int(rule.probability * 1000):
                continue
        # Resolve breed colors → form-ids.
        resolved: list[tuple[str, int]] = []  # (edid, form_id_low24)
        for edid in rule.color_edids:
            fid = form_id_for_edid(edid)
            if fid is None:
                log.warning(
                    f"breed tint color {edid!r} not found in any plugin's "
                    f"CLFM records; dropping")
                continue
            resolved.append((edid, fid & 0x00FFFFFF))
        if not resolved:
            log.warning(
                f"breed tint rule for mask {rule.mask_substring!r} has no "
                f"resolvable colors; dropping rule")
            continue
        # For each matched parent TINI, intersect the rule's resolved
        # colors with the TINI's allowed presets, hash-pick one, emit.
        for asset in matches:
            preset_low24 = {p[0] & 0x00FFFFFF: p for p in asset.presets}
            allowed = [(edid, fid_low) for edid, fid_low in resolved
                       if fid_low in preset_low24]
            if not allowed:
                log.warning(
                    f"breed colors for mask {rule.mask_substring!r} not "
                    f"among parent TINI {asset.index}'s presets; "
                    f"dropping that match")
                continue
            # Salt the color pick by both alias and mask so distinct masks
            # in the same rule pick independently.
            color_salt = 7411 + asset.index
            idx = hash_string(npc_alias, color_salt, len(allowed))
            edid, fid_low = allowed[idx]
            preset = preset_low24[fid_low]
            color_fid, intensity, tirs = preset
            choices.append(TintChoice(
                tini=asset.index,
                tinc=color_fid,
                tinv=intensity,
                tias=tirs,
            ))
    return choices


def _randomize_index_list(hash_str: str, seed: int, list_len: int) -> list[int]:
    """Deterministic permutation of [0..list_len-1], keyed on the
    (hash_str, seed) pair."""
    rng = random.Random(f"{hash_str}:{seed}")
    indices = list(range(list_len))
    rng.shuffle(indices)
    return indices
