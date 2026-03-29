"""Cats and Dogs preference scheme.

Same as All Races but without Sailors subrace.
Ported from BDFurrySkyrim_Preferences_CatsDogs.pas.
"""


def configure(ctx):
    """Configure race assignments for the Cats and Dogs scheme."""

    # Dogs (same as All Races)
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
    ctx.set_race('BretonRaceChildVampire', 'YASKettuRaceChildVampire', 'DOG')
    ctx.set_race('RedguardRace', 'YASXebaRace', 'DOG')
    ctx.set_race('RedguardRaceChild', 'YASXebaRaceChild', 'DOG')
    ctx.set_race('RedguardRaceVampire', 'YASXebaRaceVampire', 'DOG')

    # Reachman
    ctx.set_subrace('YASReachmanRace', 'Reachmen', 'BretonRace', 'YASKonoiRace', 'DOG')
    ctx.set_subrace('YASReachmanRaceVampire', 'Reachmen', 'BretonRaceVampire', 'YASKonoiRaceVampire', 'DOG')
    ctx.set_subrace('YASReachmanRaceChild', 'Reachmen', 'BretonRaceChild', 'YASKonoiRaceChild', 'DOG')
    ctx.set_faction_race('ForswornFaction', 'YASReachmanRace')
    ctx.set_faction_race('MS01TreasuryHouseForsworn', 'YASReachmanRace')
    ctx.set_faction_race('DruadachRedoubtFaction', 'YASReachmanRace')

    # Skaal
    ctx.set_subrace('YASSkaalRace', 'Skaal', 'NordRace', 'YASVaalsarkRace', 'DOG')
    ctx.set_subrace('YASSkaalRaceChild', 'Skaal', 'NordRaceChild', 'YASVaalsarkRaceChild', 'DOG')
    ctx.set_subrace('YASSkaalRaceVampire', 'Skaal', 'NordRaceVampire', 'YASVaalsarkRaceVampire', 'DOG')
    ctx.set_faction_race('DLC2SkaalVillageCitizenFaction', 'YASSkaalRace')

    # Cats
    ctx.set_race('HighElfRace', 'YASMahaRace', 'CAT')
    ctx.set_race('HighElfRaceVampire', 'YASMahaRaceVampire', 'CAT')
    ctx.set_race('WoodElfRace', 'YASDumaRace', 'CAT')
    ctx.set_race('WoodElfRaceVampire', 'YASDumaRaceVampire', 'CAT')
    ctx.set_race('DarkElfRace', 'YASKaloRace', 'CAT')
    ctx.set_race('DarkElfRaceVampire', 'YASKaloRaceVampire', 'CAT')
    ctx.set_race('OrcRace', 'YASBaghaRace', 'CAT')
    ctx.set_race('OrcRaceVampire', 'YASBaghaRaceVampire', 'CAT')
    ctx.set_race('SnowElfRace', 'YASShanRace', 'CAT')

    # Winterhold
    ctx.set_subrace('YASWinterholdRace', 'Winterhold Denizen', 'NordRace', 'YASShanRace', 'CAT')
    ctx.set_faction_race('TownWinterholdFaction', 'YASWinterholdRace')
    ctx.set_faction_race('CrimeFactionWinterhold', 'YASWinterholdRace')
