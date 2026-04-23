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


def inherits_traits(npc: Record) -> bool:
    """True if the NPC inherits appearance from its TPLT target.

    Trait-templated NPCs have empty/placeholder face data of their
    own; the game renders them using the template's facegen. We skip
    them in facegen baking (nothing useful to produce) and hide them
    from the preview picker (nothing useful to preview). Defensive
    against missing ACBS/template_flags — defaults to False.
    """
    try:
        return bool(npc["ACBS"]["template_flags"].Traits)
    except Exception:
        return False
