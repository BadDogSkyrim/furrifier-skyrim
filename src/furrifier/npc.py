"""NPC helper utilities.

Simple helpers for NPC sex/race determination that don't require
FurryContext. The main furrification logic lives in context.py.
"""

from __future__ import annotations

from typing import Optional

from esplib import Record

from .models import Sex
from .furry_load import is_npc_female, is_child_race


def determine_npc_sex(npc: Record, race: Optional[Record]) -> Sex:
    """Determine the NPC's Sex enum from ACBS flags and race."""
    female = is_npc_female(npc)
    child = is_child_race(race) if race is not None else False
    return Sex.from_flags(female=female, child=child)
