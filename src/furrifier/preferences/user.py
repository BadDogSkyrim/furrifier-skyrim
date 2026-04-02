"""User-defined preference scheme.

Edit this file to create your own race assignments.
See all_races.py for a complete example.
"""

from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from furrifier.race_defs import RaceDefContext


def configure(ctx: RaceDefContext):
    """Configure custom race assignments.

    Edit the calls below to change how vanilla races map to furry races.
    Available methods:
        ctx.set_race(vanilla_id, furry_id, class)
        ctx.set_subrace(subrace_id, display_name, vanilla_basis, furry_id, class)
        ctx.set_faction_race(faction_id, subrace_id)
        ctx.set_npc_race(npc_edid, subrace_id)
    """

    # =========== Dogs ===========
    ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('NordRaceVampire', 'YASLykaiosRaceVampire', 'DOG')
    ctx.set_race('NordRaceChild', 'YASLykaiosRaceChild', 'DOG')
    ctx.set_race('DLC1NordRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('NordRaceAstrid', 'YASLykaiosRace', 'DOG')
    ctx.set_race('ElderRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('ElderRaceVampire', 'YASLykaiosRaceVampire', 'DOG')
    ctx.set_race('ImperialRace', 'YASKettuRace', 'DOG')
    ctx.set_race('ImperialRaceChild', 'YASKettuRaceChild', 'DOG')
    ctx.set_race('ImperialRaceVampire', 'YASKettuRaceVampire', 'DOG')
    ctx.set_race('BretonRace', 'YASKygarraRace', 'DOG')
    ctx.set_race('BretonRaceChild', 'YASKygarraRaceChild', 'DOG')
    ctx.set_race('BretonRaceVampire', 'YASKygarraRaceVampire', 'DOG')
    ctx.set_race('RedguardRace', 'YASXebaRace', 'DOG')
    ctx.set_race('RedguardRaceChild', 'YASXebaRaceChild', 'DOG')
    ctx.set_race('RedguardRaceVampire', 'YASXebaRaceVampire', 'DOG')

    # Reachman
    ctx.set_subrace('YASReachmanRace', 'Reachmen', 'BretonRace', 'YASKonoiRace', 'DOG')
    ctx.set_faction_race('ForswornFaction', 'YASReachmanRace')

    # Skaal
    ctx.set_subrace('YASSkaalRace', 'Skaal', 'NordRace', 'YASVaalsarkRace', 'DOG')
    ctx.set_faction_race('DLC2SkaalVillageCitizenFaction', 'YASSkaalRace')

    # =========== Cats ===========
    ctx.set_race('HighElfRace', 'YASMahaRace', 'CAT')
    ctx.set_race('HighElfRaceVampire', 'YASMahaRaceVampire', 'CAT')
    ctx.set_race('WoodElfRace', 'YASDumaRace', 'CAT')
    ctx.set_race('WoodElfRaceVampire', 'YASDumaRaceVampire', 'CAT')
    ctx.set_race('DarkElfRace', 'YASKaloRace', 'CAT')
    ctx.set_race('DarkElfRaceVampire', 'YASKaloRaceVampire', 'CAT')
    ctx.set_race('OrcRace', 'YASBaghaRace', 'CAT')
    ctx.set_race('OrcRaceVampire', 'YASBaghaRaceVampire', 'CAT')
    ctx.set_race('SnowElfRace', 'YASShanRace', 'CAT')
