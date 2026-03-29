"""Tint layer logic for NPC furrification.

Selects furry tint layers (skin tone, fur patterns, markings) for NPCs
based on the furry race's available tint assets.
Ported from BDFurrySkyrim_Furrifier.pas tint functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from .models import Sex, TintLayer, TintAsset
from .util import hash_string

log = logging.getLogger(__name__)

# Tint layer class names in order matching TintLayer enum
TINT_CLASS_NAMES = [
    'Skin Tone', 'Cheek Color Lower', 'Chin', 'EyeSocket Lower',
    'EyeSocket Upper', 'Eyeliner', 'Forehead', 'Laugh Lines',
    'Lip Color', 'Neck', 'Nose', 'Cheek Color', 'Muzzle',
    'Stripes', 'Spots', 'Mask', 'Brow', 'Ear',
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
    key = furry_race_id
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
                    tinv=preset[1],  # default value
                    tias=idx,
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

        if layer_id == TintLayer.SKIN_TONE and not is_required:
            continue  # Already handled skin tone above

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
                    tias=preset_idx,
                ))

        if layer_id < TintLayer.DECORATION_LO:
            fur_count += 1

    return choices


def _randomize_index_list(hash_str: str, seed: int, list_len: int) -> list[int]:
    """Create a pseudo-random ordering of indices [0..list_len-1].

    Exact port of Pascal RandomizeIndexList — uses sorted insert with
    hash-based keys to produce a deterministic permutation.
    """
    if list_len <= 0:
        return []

    # Build (hash_key, original_index) pairs and sort by hash_key
    entries: list[tuple[int, int]] = []
    for i in range(list_len):
        hs = hash_str + format(i * 1000, '08X')
        hv = hash_string(hs, seed, 1000)
        entries.append((hv, i))

    # The Pascal code uses a sorted TStringList with dupIgnore,
    # which means duplicate hash values are dropped. We replicate
    # that by using a dict keyed on hash value.
    seen: dict[int, int] = {}
    for hv, idx in entries:
        if hv not in seen:
            seen[hv] = idx

    # Sort by hash value and return the indices
    return [idx for _, idx in sorted(seen.items())]
