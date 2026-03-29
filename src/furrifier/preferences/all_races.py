"""All Races preference scheme.

Maps all human/elf/orc vanilla races to furry equivalents.
Dogs for humans, cats for elves/orcs.
Ported from BDFurrySkyrim_Preferences_AllRaces.pas.
"""


def configure(ctx):
    """Configure race assignments for the All Races scheme."""

    # =========== NORD ===========
    ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('NordRaceVampire', 'YASLykaiosRaceVampire', 'DOG')
    ctx.set_race('NordRaceChild', 'YASLykaiosRaceChild', 'DOG')
    ctx.set_race('DLC1NordRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('NordRaceAstrid', 'YASLykaiosRace', 'DOG')

    # =========== ELDER ===========
    ctx.set_race('ElderRace', 'YASLykaiosRace', 'DOG')
    ctx.set_race('ElderRaceVampire', 'YASLykaiosRaceVampire', 'DOG')

    # =========== IMPERIAL ===========
    ctx.set_race('ImperialRace', 'YASKettuRace', 'DOG')
    ctx.set_race('ImperialRaceChild', 'YASKettuRaceChild', 'DOG')
    ctx.set_race('ImperialRaceVampire', 'YASKettuRaceVampire', 'DOG')

    # =========== BRETON ===========
    ctx.set_race('BretonRace', 'YASKygarraRace', 'DOG')
    ctx.set_race('BretonRaceChild', 'YASKygarraRaceChild', 'DOG')
    ctx.set_race('BretonRaceVampire', 'YASKygarraRaceVampire', 'DOG')
    ctx.set_race('BretonRaceChildVampire', 'YASKettuRaceChildVampire', 'DOG')

    # =========== REDGUARD ===========
    ctx.set_race('RedguardRace', 'YASXebaRace', 'DOG')
    ctx.set_race('RedguardRaceChild', 'YASXebaRaceChild', 'DOG')
    ctx.set_race('RedguardRaceVampire', 'YASXebaRaceVampire', 'DOG')

    # =========== REACHMAN (subrace of Breton) ===========
    ctx.set_subrace('YASReachmanRace', 'Reachmen', 'BretonRace', 'YASKonoiRace', 'DOG')
    ctx.set_subrace('YASReachmanRaceVampire', 'Reachmen', 'BretonRaceVampire', 'YASKonoiRaceVampire', 'DOG')
    ctx.set_subrace('YASReachmanRaceChild', 'Reachmen', 'BretonRaceChild', 'YASKonoiRaceChild', 'DOG')
    ctx.set_faction_race('ForswornFaction', 'YASReachmanRace')
    ctx.set_faction_race('MS01TreasuryHouseForsworn', 'YASReachmanRace')
    ctx.set_faction_race('DruadachRedoubtFaction', 'YASReachmanRace')

    # =========== SKAAL (subrace of Nord) ===========
    ctx.set_subrace('YASSkaalRace', 'Skaal', 'NordRace', 'YASVaalsarkRace', 'DOG')
    ctx.set_subrace('YASSkaalRaceChild', 'Skaal', 'NordRaceChild', 'YASVaalsarkRaceChild', 'DOG')
    ctx.set_subrace('YASSkaalRaceVampire', 'Skaal', 'NordRaceVampire', 'YASVaalsarkRaceVampire', 'DOG')
    ctx.set_faction_race('DLC2SkaalVillageCitizenFaction', 'YASSkaalRace')

    # =========== HIGH ELF ===========
    ctx.set_race('HighElfRace', 'YASMahaRace', 'CAT')
    ctx.set_race('HighElfRaceVampire', 'YASMahaRaceVampire', 'CAT')

    # =========== WOOD ELF ===========
    ctx.set_race('WoodElfRace', 'YASDumaRace', 'CAT')
    ctx.set_race('WoodElfRaceVampire', 'YASDumaRaceVampire', 'CAT')

    # =========== DARK ELF ===========
    ctx.set_race('DarkElfRace', 'YASKaloRace', 'CAT')
    ctx.set_race('DarkElfRaceVampire', 'YASKaloRaceVampire', 'CAT')

    # =========== ORC ===========
    ctx.set_race('OrcRace', 'YASBaghaRace', 'CAT')
    ctx.set_race('OrcRaceVampire', 'YASBaghaRaceVampire', 'CAT')

    # =========== SNOW ELF ===========
    ctx.set_race('SnowElfRace', 'YASShanRace', 'CAT')

    # =========== WINTERHOLD (subrace of Nord, assigned cat) ===========
    ctx.set_subrace('YASWinterholdRace', 'Winterhold Denizen', 'NordRace', 'YASShanRace', 'CAT')
    ctx.set_faction_race('TownWinterholdFaction', 'YASWinterholdRace')
    ctx.set_faction_race('CrimeFactionWinterhold', 'YASWinterholdRace')

    # =========== SAILORS (subrace of Nord, assigned cat) ===========
    ctx.set_subrace('YASSailorRace', 'Sailor', 'NordRace', 'CellanRace', 'CAT')
    ctx.set_faction_race('DawnstarFishingShipFaction', 'YASSailorRace')
    ctx.set_faction_race('DawnstarImperialShipFaction', 'YASSailorRace')
    ctx.set_faction_race('DawnstarSmallShipFaction', 'YASSailorRace')
    ctx.set_faction_race('DLC2WaterStoneSailors', 'YASSailorRace')
    ctx.set_faction_race('SailorFaction', 'YASSailorRace')
    ctx.set_faction_race('ShipsNorthwindFaction', 'YASSailorRace')
    ctx.set_faction_race('ShipsRedWaveFaction', 'YASSailorRace')
    ctx.set_faction_race('ShipsSeaSquallFaction', 'YASSailorRace')
    ctx.set_npc_race('Jolf', 'YASSailorRace')
