"""Utility functions ported from BDScriptTools.pas.

Hash functions, color helpers, and bodypart flag operations.
"""

from __future__ import annotations

import logging

from esplib import Record

log = logging.getLogger(__name__)


def get_bodypart_flags(arma: Record) -> int:
    """Get bodypart flags from an ARMA's BOD2 subrecord."""
    bod2 = arma.get_subrecord('BOD2')
    if bod2 and bod2.size >= 4:
        return bod2.get_uint32(0)
    # Fall back to old BODT format
    bodt = arma.get_subrecord('BODT')
    if bodt and bodt.size >= 4:
        return bodt.get_uint32(0)
    return 0


def short_race_name(edid: str) -> str:
    """Shorten a race EditorID for use in composed identifiers.

    Strips a leading 'YAS' prefix; replaces the 'RaceVampire' suffix
    with 'V'; otherwise strips a trailing 'Race'.
    """
    name = edid
    if name.startswith('YAS'):
        name = name[3:]
    if name.endswith('RaceVampire'):
        name = name[:-len('RaceVampire')] + 'V'
    elif name.endswith('Race'):
        name = name[:-4]
    return name


def hash_string(s: str, seed: int, m: int) -> int:
    """Hash a string with seed, return result modulo m.

    Exact port of the Pascal Hash() function for deterministic NPC
    selection. Different seed values ensure that even if two NPCs hash
    the same, not every aspect of them will be identical.
    """
    h = seed
    for c in s:
        h = ((31 * h) + ord(c)) % 16000
    h = (31 * h) % 16000
    if m == 0:
        return 0
    return h % m


def hash_val(s: str, seed: int, min_val: float, max_val: float) -> float:
    """Hash-based pseudo-random float in [min_val, max_val]."""
    return hash_string(s, seed, int((max_val - min_val) * 100 + 1)) / 100 + min_val


def hash_int(s: str, seed: int, min_val: int, max_val: int) -> int:
    """Hash-based pseudo-random int in [min_val, max_val)."""
    return hash_string(s, seed, max_val - min_val) + min_val


# Seed for range picks. Distinct from the race/headpart/tint/weight seeds so a
# ranged value doesn't correlate with the choices made for the same NPC.
_RANGE_SEED = 7717


def parse_range(val, ctx: str = '', default=None):
    """Normalize a catalog scalar to a `(lo, hi)` range.

    Every number in a catalog may be written either bare (`0.5`) or as a
    two-element range (`[0.2, 0.8]`) that each NPC draws its own value
    from. A bare number is the degenerate range `(0.5, 0.5)`, so callers
    resolve both forms through one path -- there is no "is it ranged?"
    branch anywhere downstream.

    Returns `default` (and warns, if `ctx` is given) for anything that is
    neither. `lo > hi` is accepted and swapped.
    """
    if isinstance(val, bool):           # bool is an int subclass; not a scalar
        pass
    elif isinstance(val, (int, float)):
        return (float(val), float(val))
    elif _is_number_pair(val):
        lo, hi = float(val[0]), float(val[1])
        return (lo, hi) if lo <= hi else (hi, lo)
    if ctx:
        log.warning("%s: expected a number or a [lo, hi] range, got %r; "
                    "ignored", ctx, val)
    return default


def parse_probability(val, ctx: str = '', default=None):
    """A catalog probability: a plain number, never a range.

    Ranges belong on values written ONTO the NPC (`parse_range`), not on
    probabilities -- drawing a random probability per NPC and then rolling
    against it is distributionally identical to rolling against the
    midpoint, so a range here would be a knob that does nothing. Reject it
    with a message naming the number to use instead, rather than crash on
    `float(a_list)` or silently accept it.

    Returns `default` (warning, if `ctx` is given) for anything else.
    """
    if isinstance(val, bool):           # bool is an int subclass; not a number
        pass
    elif isinstance(val, (int, float)):
        return float(val)
    elif ctx and _is_number_pair(val):
        log.warning("%s: a probability takes a plain number, not a range -- "
                    "%r would behave exactly like %g; use that instead. Ignored",
                    ctx, list(val), (float(val[0]) + float(val[1])) / 2)
        return default
    if ctx:
        log.warning("%s: expected a probability (a number), got %r; ignored",
                    ctx, val)
    return default


def _is_number_pair(val) -> bool:
    return (isinstance(val, (list, tuple)) and len(val) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in val))


def pick_range(rng, signature: str, key: str = '') -> float:
    """Draw this NPC's value from a `(lo, hi)` range.

    Deterministic on the NPC's `signature` (the project-wide invariant), so
    a run always reproduces. `key` names the field being drawn -- two
    ranges on the same NPC decorrelate only if their keys differ, so pass
    something field-specific (`"tint.YASFurWhite"`, not `"tint"`).
    """
    lo, hi = rng
    if lo == hi:
        return lo
    frac = hash_string(f"{signature}|{key}", _RANGE_SEED, 1001) / 1000.0
    return lo + frac * (hi - lo)


def red_part(rgb: int) -> int:
    """Extract red component from a packed RGB value."""
    return rgb & 0xFF


def green_part(rgb: int) -> int:
    """Extract green component from a packed RGB value."""
    return (rgb >> 8) & 0xFF


def blue_part(rgb: int) -> int:
    """Extract blue component from a packed RGB value."""
    return (rgb >> 16) & 0xFF


def alpha_part(rgb: int) -> float:
    """Extract alpha component from a packed RGBA value (0.0-1.0)."""
    return ((rgb >> 24) & 0xFF) / 255.0
