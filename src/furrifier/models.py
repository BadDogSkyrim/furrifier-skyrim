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
    HAIR = 0
    SCAR = 1
    EYES = 2
    EYEBROWS = 3
    FACIAL_HAIR = 4
    UNKNOWN = 5


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
    STRIPES = 13
    SPOTS = 14
    MASK = 15
    BROW = 16
    EAR = 17
    # Decoration layers (only applied if the NPC already has them)
    BLACKBLOOD = 18
    BOTHIAH = 19
    FORSWORN = 20
    FRECKLES = 21
    NORD = 22
    DARKELF = 23
    IMPERIAL = 24
    ORC = 25
    REDGUARD = 26
    WOODELF = 27
    HAND = 28
    SKULL = 29
    PAINT = 30
    DIRT = 31

    DECORATION_LO = 18  # First decoration layer index
    COUNT = 32


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
    presets: list = field(default_factory=list)  # List of (color_fid, default_value)


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
    furry_class: str        # Race class (e.g. 'dog', 'cat')
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
    furry_class: str        # Race class


@dataclass
class HeadpartInfo:
    """Metadata about a headpart record."""
    record: Record
    editor_id: str
    hp_type: HeadpartType
    labels: list = field(default_factory=list)
    equivalents: list = field(default_factory=list)  # list of EditorID strings
