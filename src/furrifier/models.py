"""Data models for the Furrifier.

Replaces the Pascal TStringList-of-TStringList pattern with typed dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag
from typing import Optional

from esplib import Record, FormID


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Sex(IntEnum):
    MALE_ADULT = 0
    FEMALE_ADULT = 1
    MALE_CHILD = 2
    FEMALE_CHILD = 3

    @property
    def is_female(self) -> bool:
        return bool(self.value & 1)

    @property
    def is_child(self) -> bool:
        return bool(self.value & 2)

    @classmethod
    def from_flags(cls, female: bool, child: bool) -> Sex:
        return cls((int(female)) | (int(child) << 1))


class HeadpartType(IntEnum):
    """Headpart types matching xEdit's enumeration."""
    MISC = 0
    FACE = 1
    EYES = 2
    HAIR = 3
    FACIAL_HAIR = 4
    SCAR = 5
    EYEBROWS = 6
    UNKNOWN = 99


class TintLayer(IntEnum):
    SKIN_TONE = 0
    CHEEK_LOWER = 1
    CHIN = 2
    EYE_LOWER = 3
    EYE_UPPER = 4
    EYELINER = 5
    FOREHEAD = 6
    LAUGH = 7
    LIP = 8
    NECK = 9
    NOSE = 10
    CHEEK = 11
    MUZZLE = 12
    MUSTACHE = 13
    STRIPES = 14
    SPOTS = 15
    MASK = 16
    BROW = 17
    EAR = 18
    # Decoration layers (only applied if the NPC already has them)
    BLACKBLOOD = 19
    BOTHIAH = 20
    FORSWORN = 21
    FRECKLES = 22
    NORD = 23
    DARKELF = 24
    IMPERIAL = 25
    ORC = 26
    REDGUARD = 27
    WOODELF = 28
    HAND = 29
    SKULL = 30
    PAINT = 31
    DIRT = 32

    DECORATION_LO = 19  # First decoration layer index
    COUNT = 33


class Bodypart(IntFlag):
    HEAD = 1
    HAIR = 2
    HANDS = 8
    LONGHAIR = 0x800
    CIRCLET = 0x1000
    SCHLONG = 0x400000


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TintAsset:
    """One tint mask entry from a race's head data."""
    index: int              # TINI value
    filename: str           # TINT file path
    layer_type: int         # Resolved tint layer type
    layer_class: str        # Class name (e.g. 'SKIN_TONE', 'MUZZLE')
    presets: list = field(default_factory=list)  # List of (color_fid, intensity, tirs)


@dataclass
class RaceInfo:
    """All pre-indexed data about a race."""
    record: Record
    editor_id: str
    is_child: bool = False
    race_class: str = ''    # e.g. 'dog', 'cat'

    # Headparts indexed by sex and type
    # headparts[Sex.MALE_ADULT][HeadpartType.HAIR] -> list[Record]
    headparts: dict = field(default_factory=dict)

    # Tint assets indexed by sex
    # tints[Sex.MALE_ADULT] -> list[TintAsset]
    tints: dict = field(default_factory=dict)


@dataclass
class RaceAssignment:
    """Maps a vanilla race to its furry replacement."""
    vanilla_id: str         # Vanilla race EditorID (e.g. 'NordRace')
    furry_id: str           # Furry race EditorID (e.g. 'BDLykaiosRace')
    labels: list = field(default_factory=list)

    # Populated during setup
    vanilla: Optional[RaceInfo] = None
    furry: Optional[RaceInfo] = None


@dataclass
class Subrace:
    """A subrace derived from a vanilla race with different furry assignment."""
    name: str               # e.g. 'Reachman'
    display_name: str       # e.g. 'Reachman'
    vanilla_basis: str      # Base vanilla race EditorID
    furry_id: str           # Furry race EditorID


@dataclass
class Breed:
    """A constrained visual flavor of a parent furry race.

    Reuses the parent's RACE record at the engine level — the breed
    only affects which headparts/tints the furrifier picks. Phase 1
    just registers the breed and exposes it through scheme resolution;
    Phase 2/3 add headpart_rules and tint_rules. See
    PLAN_FURRIFIER_BREEDS.md.
    """
    name: str                       # e.g. 'Cougar'
    parent_race_edid: str           # e.g. 'YASKaloRace'
    probability: float = 0.0        # auto-roll weight; 0.0 = explicit-only


@dataclass
class LeveledNpcEntry:
    """Per-race rule inside a leveled-list group.

    For every existing LVLO entry whose source NPC's race is in
    ``race_assignments``, roll ``random() < probability``. On hit,
    duplicate the source NPC and assign it to ``race``.
    """
    race: str               # Furry race EditorID (e.g. 'YASKonoiRace')
    probability: float      # Per-(entry, race) duplicate probability


@dataclass
class LeveledNpcGroup:
    """A group of leveled-list extension rules with a match filter.

    First-match-wins: groups are tried in order against each LVLN's
    editor_id. The first group whose ``match_substrings`` matches (case-
    insensitive substring) supplies the race rules for that list. An
    empty/missing ``match_substrings`` matches any list, so place a
    catch-all group last if you want one.
    """
    match_substrings: list[str]
    races: list[LeveledNpcEntry]

    def matches(self, lvln_editor_id: str) -> bool:
        if not self.match_substrings:
            return True
        eid_lower = lvln_editor_id.lower()
        return any(s.lower() in eid_lower for s in self.match_substrings)


@dataclass
class HeadpartInfo:
    """Metadata about a headpart record."""
    record: Record
    editor_id: str
    hp_type: HeadpartType
    labels: list = field(default_factory=list)
    equivalents: list = field(default_factory=list)  # list of EditorID strings
