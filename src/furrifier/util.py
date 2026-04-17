"""Utility functions ported from BDScriptTools.pas.

Hash functions, color helpers, and bodypart flag operations.
"""


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
