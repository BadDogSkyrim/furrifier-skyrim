"""Legacy preference scheme.

Original default mappings with different elf/human assignments.
Ported from BDFurrySkyrim_Preferences_Legacy.pas.
"""


def configure(ctx):
    """Configure race assignments for the Legacy scheme."""

    # Nord (same as all schemes)
    ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('NordRaceVampire', 'YASLykaiosRaceVampire', 'DOG')
    ctx.set_race('NordRaceChild', 'YASLykaiosRaceChild', 'DOG')
    ctx.set_race('DLC1NordRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('NordRaceAstrid', 'YASLykaiosRace', 'DOG')
    ctx.set_race('ElderRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('ElderRaceVampire', 'YASLykaiosRaceVampire', 'DOG')

    # Imperial -> Vaalsark (not Kettu)
    ctx.set_race('ImperialRace', 'YASVaalsarkRace', 'DOG')
    ctx.set_race('ImperialRaceChild', 'YASVaalsarkRaceChild', 'DOG')
    ctx.set_race('ImperialRaceVampire', 'YASVaalsarkRaceVampire', 'DOG')

    # Breton -> Kettu (not Kygarra)
    ctx.set_race('BretonRace', 'YASKettuRace', 'DOG')
    ctx.set_race('BretonRaceChild', 'YASKettuRaceChild', 'DOG')
    ctx.set_race('BretonRaceVampire', 'YASKettuRaceVampire', 'DOG')
    ctx.set_race('BretonRaceChildVampire', 'YASKettuRaceChildVampire', 'DOG')

    # Redguard -> Kygarra (not Xeba)
    ctx.set_race('RedguardRace', 'YASKygarraRace', 'DOG')
    ctx.set_race('RedguardRaceChild', 'YASKygarraRaceChild', 'DOG')
    ctx.set_race('RedguardRaceVampire', 'YASKygarraRaceVampire', 'DOG')

    # Reachman (same)
    ctx.set_subrace('YASReachmanRace', 'Reachmen', 'BretonRace', 'YASKonoiRace', 'DOG')
    ctx.set_subrace('YASReachmanRaceVampire', 'Reachmen', 'BretonRaceVampire', 'YASKonoiRaceVampire', 'DOG')
    ctx.set_subrace('YASReachmanRaceChild', 'Reachmen', 'BretonRaceChild', 'YASKonoiRaceChild', 'DOG')
    ctx.set_faction_race('ForswornFaction', 'YASReachmanRace')
    ctx.set_faction_race('MS01TreasuryHouseForsworn', 'YASReachmanRace')
    ctx.set_faction_race('DruadachRedoubtFaction', 'YASReachmanRace')

    # Skaal -> Xeba (not Vaalsark)
    ctx.set_subrace('YASSkaalRace', 'Skaal', 'NordRace', 'YASXebaRace', 'DOG')
    ctx.set_subrace('YASSkaalRaceChild', 'Skaal', 'NordRaceChild', 'YASXebaRaceChild', 'DOG')
    ctx.set_subrace('YASSkaalRaceVampire', 'Skaal', 'NordRaceVampire', 'YASXebaRaceVampire', 'DOG')
    ctx.set_faction_race('DLC2SkaalVillageCitizenFaction', 'YASSkaalRace')

    # High Elf -> Duma (not Maha)
    ctx.set_race('HighElfRace', 'YASDumaRace', 'CAT')
    ctx.set_race('HighElfRaceVampire', 'YASDumaRaceVampire', 'CAT')

    # Wood Elf -> Bagha (not Duma)
    ctx.set_race('WoodElfRace', 'YASBaghaRace', 'CAT')
    ctx.set_race('WoodElfRaceVampire', 'YASBaghaRaceVampire', 'CAT')

    # Dark Elf (same)
    ctx.set_race('DarkElfRace', 'YASKaloRace', 'CAT')
    ctx.set_race('DarkElfRaceVampire', 'YASKaloRaceVampire', 'CAT')

    # Orc -> Maha (not Bagha)
    ctx.set_race('OrcRace', 'YASMahaRace', 'CAT')
    ctx.set_race('OrcRaceVampire', 'YASMahaRaceVampire', 'CAT')

    # Snow Elf
    ctx.set_race('SnowElfRace', 'YASShanRace', 'CAT')
