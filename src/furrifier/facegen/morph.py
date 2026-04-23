"""
Phase 4: apply chargen morphs to facegen shape verts.

The algorithm per shape:

  verts = source_nif.verts                                       # from headpart NIF
  if race_tri has morph matching NPC's RNAM edid:
      verts += race_tri[race_edid] - race_tri['Basis']           # coefficient 1.0
  if chargen_tri exists:
      for slot i in 0..17:
          v = NAM9[i]
          pos, neg = SLOT_MAP[i]
          morph = pos if v >= 0 else neg
          if morph is not None and morph in chargen_tri:
              verts += v * (chargen_tri[morph] - chargen_tri['Basis'])
      # slot 18 is Vampiremorph; FLT_MAX means not-set, skip

We compute deltas from the tri file (morph - Basis) rather than using
the absolute morph positions. That way mod authors who pair a vanilla
tri with a different base mesh still get consistent morph behavior.

Missing tri files and missing morph names log a warning via the `pynifly`
logger at WARNING level and the shape proceeds with whatever deltas it
could apply. That's intentional graceful degradation.
"""
from __future__ import annotations

import importlib.util
import logging
import math
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np


log = logging.getLogger("furrifier.facegen.morph")


# Load PyNifly's TriFile module once at import time — bypassing the
# package's bpy-tainted __init__. Previously happened per-call, which
# dominated the tri-load cost when many NPCs share headparts.
_tri_spec = importlib.util.spec_from_file_location(
    "_furrifier_trifile",
    r"C:\Modding\PyNifly\io_scene_nifly\tri\trifile.py",
)
_tri_module = importlib.util.module_from_spec(_tri_spec)
_tri_spec.loader.exec_module(_tri_module)
_TriFile = _tri_module.TriFile


# NAM9 slot → (positive morph name, negative morph name).
# Names are None when a slot has only one direction (Vampiremorph).
# Derived empirically from the chargen tri content + Hugh's xEdit
# slot-name reference. If a morph doesn't exist in a given tri, we
# warn and skip — works fine for reduced mod tris.
SLOT_MAP: list[tuple[Optional[str], Optional[str]]] = [
    ("NoseLong",    "NoseShort"),      # 0: Nose long/short
    ("NoseUp",      "NoseDown"),       # 1: Nose up/down
    ("JawDown",     "JawUp"),          # 2: Jaw up/down (+=down per lstsq fit)
    ("JawWide",     "JawNarrow"),      # 3: Jaw narrow/wide
    ("JawForward",  "JawBack"),        # 4: Jaw forward/back
    ("CheeksUp",    "CheeksDown"),     # 5: Cheeks up/down
    ("CheeksOut",   "CheeksIn"),       # 6: Cheeks forward/back (+=forward/out)
    ("EyesMoveUp",  "EyesMoveDown"),   # 7: Eyes up/down
    ("EyesMoveOut", "EyesMoveIn"),     # 8: Eyes in/out (+=out per empirical fit)
    ("BrowUp",      "BrowDown"),       # 9: Brows up/down
    ("BrowOut",     "BrowIn"),         # 10: Brows in/out (+=out)
    ("BrowForward", "BrowBack"),       # 11: Brows forward/back
    ("LipMoveUp",   "LipMoveDown"),    # 12: Lips up/down
    ("LipMoveOut", "LipMoveIn"),       # 13: Lips in/out (+=out)
    ("ChinWide",    "ChinThin"),       # 14: Chin narrow/wide (+=wide)
    ("ChinMoveDown", "ChinMoveUp"),    # 15: Chin up/down (+=down per lstsq fit)
    ("Underbite",   "Overbite"),       # 16: Chin underbite/overbite
    ("EyesForward", "EyesBack"),       # 17: Eyes forward/back
    ("VampireMorph", None),            # 18: Vampiremorph (single direction)
]


@lru_cache(maxsize=64)
def _load_trifile(path: Path):
    """Parse a .tri file. Cached: a batch-facegen run across many NPCs
    repeatedly loads the same race/chargen/behavior tris (e.g. every
    wood elf pulls the same three)."""
    with open(path, "rb") as f:
        return _TriFile.from_file(f)


def _tri_delta(tri, morph_name: str) -> Optional[np.ndarray]:
    """Return (N, 3) delta = morph - Basis, or None if the morph is absent."""
    if morph_name not in tri.morphs or "Basis" not in tri.morphs:
        return None
    return (np.asarray(tri.morphs[morph_name], dtype=np.float32)
            - np.asarray(tri.morphs["Basis"], dtype=np.float32))


def apply_morphs(
    verts: np.ndarray,
    race_tri_path: Optional[Path],
    race_edid: Optional[str],
    chargen_tri_path: Optional[Path],
    nam9: Optional[list[float]],
    behavior_tri_path: Optional[Path] = None,
    weight: Optional[float] = None,
    nama: Optional[list[int]] = None,
    shape_name: str = "<shape>",
) -> np.ndarray:
    """Return a new (N, 3) float32 vert array with race + chargen +
    weight (SkinnyMorph) morphs applied on top of the input verts.

    race_tri_path / chargen_tri_path / behavior_tri_path: Path to the
        corresponding tri file, or None if the headpart doesn't
        reference that kind of tri.
    race_edid: NPC's race EditorID (e.g. 'WoodElfRace'). Used to select
        the morph in race_tri.
    nam9: 19 floats (slots 0-17 sliders, slot 18 vampiremorph sentinel),
        or None if the NPC has no NAM9.
    weight: NPC's NAM7 weight (0-100). When the behavior tri contains
        a 'SkinnyMorph', it's applied with coefficient (100-weight)/100.
        At weight=100 the morph has no effect; at weight=0 it's full.

    Any missing tri file, missing morph name, or mismatched vert count
    produces a warning (via the furrifier.facegen.morph logger) and is
    skipped. A shape with no applicable morphs returns verts unchanged.
    """
    out = np.asarray(verts, dtype=np.float32).copy()

    # Race morph — pick the morph whose name matches the NPC's RNAM edid.
    if race_tri_path is not None and race_edid is not None:
        if not race_tri_path.is_file():
            log.warning("[%s] race tri missing: %s", shape_name, race_tri_path)
        else:
            try:
                tri = _load_trifile(race_tri_path)
            except Exception as e:
                log.warning("[%s] failed to load race tri %s: %s",
                            shape_name, race_tri_path, e)
                tri = None
            if tri is not None:
                delta = _tri_delta(tri, race_edid)
                if delta is None:
                    log.warning(
                        "[%s] race tri %s has no morph named %r (available: %s)",
                        shape_name, race_tri_path.name, race_edid,
                        ", ".join(list(tri.morphs.keys())[:6]) + "..."
                    )
                elif delta.shape != out.shape:
                    log.warning(
                        "[%s] race tri %s delta shape %s != verts shape %s",
                        shape_name, race_tri_path.name, delta.shape, out.shape
                    )
                else:
                    out += delta

    # Chargen morphs — NAM9-driven.
    if chargen_tri_path is not None and nam9 is not None:
        if not chargen_tri_path.is_file():
            log.warning("[%s] chargen tri missing: %s",
                        shape_name, chargen_tri_path)
        else:
            try:
                tri = _load_trifile(chargen_tri_path)
            except Exception as e:
                log.warning("[%s] failed to load chargen tri %s: %s",
                            shape_name, chargen_tri_path, e)
                tri = None
            if tri is not None:
                out = _apply_chargen_slots(out, tri, nam9, shape_name,
                                           chargen_tri_path.name)
                if nama is not None:
                    out = _apply_nama_presets(out, tri, nama, shape_name,
                                              chargen_tri_path.name)

    # Weight morph — CK's facegen bake multiplies 'SkinnyMorph' from the
    # behavior tri by (100 - weight) / 100 and adds it. At weight=100 no
    # contribution; at weight=0 full SkinnyMorph. Only the head's
    # behavior tri carries this morph — eye/mouth/hair behavior tris
    # don't have it, so the missing-morph path just silently no-ops.
    if (behavior_tri_path is not None and weight is not None
            and behavior_tri_path.is_file()):
        try:
            tri = _load_trifile(behavior_tri_path)
        except Exception as e:
            log.warning("[%s] failed to load behavior tri %s: %s",
                        shape_name, behavior_tri_path, e)
            tri = None
        if tri is not None:
            delta = _tri_delta(tri, "SkinnyMorph")
            if delta is not None and delta.shape == out.shape:
                coeff = (100.0 - float(weight)) / 100.0
                out = out + coeff * delta

    return out


# NAMA slot index → chargen tri morph-name prefix. Slot 1 appears to be
# unused (vanilla NPCs always carry -1 there); whatever it picks doesn't
# seem to land in the chargen tri.
_NAMA_PREFIXES = ["NoseType", None, "EyesType", "LipType"]


def _apply_nama_presets(verts: np.ndarray, tri, nama: list[int],
                        shape_name: str, tri_name: str) -> np.ndarray:
    """Apply the four preset-type morphs selected by NAMA indices.

    NAMA carries four int32 indices; for each non-negative index N at
    slot i, we look up `{_NAMA_PREFIXES[i]}{N}` in the chargen tri and
    add its delta at coefficient 1.0. Missing morphs are silently
    skipped — reduced mod tris often drop the long preset lists."""
    out = verts
    for i, prefix in enumerate(_NAMA_PREFIXES):
        if i >= len(nama) or prefix is None:
            continue
        idx = nama[i]
        if idx < 0:
            continue
        morph_name = f"{prefix}{idx}"
        delta = _tri_delta(tri, morph_name)
        if delta is None:
            continue
        if delta.shape != out.shape:
            log.warning("[%s] chargen %s preset %r shape %s != verts %s",
                        shape_name, tri_name, morph_name, delta.shape, out.shape)
            continue
        out = out + delta
    return out


def _apply_chargen_slots(verts: np.ndarray, tri, nam9: list[float],
                         shape_name: str, tri_name: str) -> np.ndarray:
    """Apply each NAM9 slider to its (pos, neg) morph pair per SLOT_MAP."""
    out = verts
    for slot_idx, (pos_name, neg_name) in enumerate(SLOT_MAP):
        if slot_idx >= len(nam9):
            break
        v = nam9[slot_idx]
        # Skip NaN / inf / the FLT_MAX sentinel at slot 18
        if not math.isfinite(v) or abs(v) > 1e6:
            continue
        # Choose direction by sign; coefficient magnitude is |v|, but we
        # apply with signed coefficient so single-direction morphs (like
        # Vampiremorph) still work with negative values.
        if v == 0.0:
            continue
        morph_name = pos_name if v >= 0 else neg_name
        coeff = v if v >= 0 else -v  # magnitude for the chosen direction
        if morph_name is None:
            # Single-direction slot with no opposite; apply the one
            # direction with signed coefficient.
            other = pos_name if pos_name else neg_name
            if other is None:
                continue
            morph_name = other
            coeff = v
        delta = _tri_delta(tri, morph_name)
        if delta is None:
            # Common and expected in reduced mod tris; don't warn every
            # slot. Only warn if the morph name is one we expect the
            # vanilla tri to always have.
            continue
        if delta.shape != out.shape:
            log.warning("[%s] chargen %s morph %r shape %s != verts %s",
                        shape_name, tri_name, morph_name, delta.shape, out.shape)
            continue
        out = out + coeff * delta
    return out
