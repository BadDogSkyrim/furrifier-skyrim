"""Vanilla game data configuration.

Hair labels, scar labels, empty headparts, label conflicts, NPC aliases,
and NPC race assignments. Ported from BDFurrySkyrimSetup.pas.
"""

from __future__ import annotations

from .race_defs import RaceDefContext


def setup_vanilla(ctx: RaceDefContext) -> None:
    """Configure all vanilla data on the context."""
    define_label_conflicts(ctx)
    define_vanilla_hair_labels(ctx)
    define_scars(ctx)
    define_empty_headparts(ctx)
    define_npc_aliases(ctx)
    assign_npc_races(ctx)


def define_vanilla_hair_labels(ctx: RaceDefContext) -> None:
    """Label all vanilla hair headparts for matching."""
    lbl = ctx.label_headpart_list

    # Elder
    lbl('HairMaleElder1', 'SHORT,NEAT,BALDING,MATURE,MILITARY')
    lbl('HairMaleElder2', 'SHORT,NEAT,BALDING,MATURE,MILITARY')
    lbl('HairMaleElder3', 'SHORT,NEAT,BALDING,MATURE,MILITARY')
    lbl('HairMaleElder4', 'SHORT,BALDING,MATURE')
    lbl('HairMaleElder5', 'SHORT,NEAT,BALDING,MATURE,MILITARY')
    lbl('HairMaleElder6', 'LONG,NEAT,BALDING,MATURE,TIEDBACK')

    # Imperial
    lbl('HairMaleImperial1', 'SHORT,NEAT,MILITARY,IMPERIAL')
    lbl('HairFemaleImperial1', 'SHORT,NEAT,MILITARY,IMPERIAL')

    # Nord Male
    lbl('HairMaleNord01', 'LONG')
    lbl('HairMaleNord02', 'LONG,BRAIDS')
    lbl('HairMaleNord03', 'NOBLE,LONG,BRAIDS')
    lbl('HairMaleNord04', 'MESSY,LONG,BRAIDS')
    lbl('HairMaleNord05', 'NEAT,LONG,TIEDBACK')
    lbl('HairMaleNord06', 'BOLD,BRAIDS')
    lbl('HairMaleNord07', 'SHORT,NEAT')
    lbl('HairMaleNord08', 'FUNKY,BRAIDS,SHORT')
    lbl('HairMaleNord09', 'NEAT,SHORT,MILITARY')
    lbl('HairMaleNord10', 'NEAT,SHORT,BRAIDS')
    lbl('HairMaleNord11', 'NEAT,SHORT,TIEDBACK,BRAIDS')
    lbl('HairMaleNord12', 'NEAT,LONG,TIEDBACK')
    lbl('HairMaleNord13', 'LONG,BOLD,YOUNG,TIEDBACK')
    lbl('HairMaleNord14', 'SHORT,NEAT,MILITARY')
    lbl('HairMaleNord15', 'SHORT,MESSY')
    lbl('HairMaleNord16', 'MOHAWK,BRAIDS,BOLD,FUNKY')
    lbl('HairMaleNord17', 'MOHAWK,BOLD,BRAIDS,FUNKY')
    lbl('HairMaleNord18', 'LONG,MESSY')
    lbl('HairMaleNord19', 'LONG,MESSY,MATURE')
    lbl('HairMaleNord20', 'MOHAWK,LONG,BOLD,FUNKY')

    # Nord Female + DLC1
    lbl('DLC1HairFemaleSerana', 'TIEDBACK,ELABORATE')
    lbl('DLC1HairFemaleSeranaHuman', 'TIEDBACK,ELABORATE')
    lbl('DLC1HairFemaleValerica', 'BUN,TIEDBACK,ELABORATE,MATURE')
    lbl('HairFemaleNord01', 'SHORT,MESSY')
    lbl('HairFemaleNord02', 'SHORT,BRAIDS')
    lbl('HairFemaleNord03', 'LONG,BRAIDS,TIEDBACK,BOLD')
    lbl('HairFemaleNord04', 'MESSY,BRAIDS')
    lbl('HairFemaleNord05', 'TIEDBACK,MESSY')
    lbl('HairFemaleNord06', 'BRAIDS')
    lbl('HairFemaleNord07', 'SHORT')
    lbl('HairFemaleNord08', 'SHORT,BRAIDS,FUNKY')
    lbl('HairFemaleNord09', 'TIEDBACK,BRAIDS')
    lbl('HairFemaleNord10', 'BRAIDS,TIEDBACK,ELABORATE')
    lbl('HairFemaleNord11', 'TIEDBACK,BRAIDS,YOUNG')
    lbl('HairFemaleNord12', 'TIEDBACK,NEAT')
    lbl('HairFemaleNord13', 'SHORT,BRAIDS')
    lbl('HairFemaleNord14', 'LONG,TIEDBACK')
    lbl('HairFemaleNord15', 'SHORT,NEAT')
    lbl('HairFemaleNord16', 'MOHAWK,BRAIDS,BOLD,FUNKY')
    lbl('HairFemaleNord17', 'SHORT,TIEDBACK,BRAIDS,ELABORATE')
    lbl('HairFemaleNord18', 'LONG,MESSY')
    lbl('HairFemaleNord19', 'SHORT,TIEDBACK,BRAIDS')
    lbl('HairFemaleNord20', 'SHORT,TIEDBACK')
    lbl('HairFemaleNord21', 'DREADS,FUNKY,BOLD,MOHAWK')

    # Orc Male
    lbl('HairMaleOrc01', 'TIEDBACK,LONG')
    lbl('HairMaleOrc02', 'TIEDBACK,NEAT,SHORT')
    lbl('HairMaleOrc03', 'TIEDBACK,NEAT,SHORT')
    lbl('HairMaleOrc04', 'TIEDBACK,NEAT,SHORT')
    lbl('HairMaleOrc05', 'TIEDBACK,NEAT,LONG')
    lbl('HairMaleOrc06', 'TIEDBACK,NEAT,LONG,BRAIDS')
    lbl('HairMaleOrc07', 'TIEDBACK,NEAT,LONG')
    lbl('HairMaleOrc09', 'NEAT,MATURE,TIEDBACK')
    lbl('HairMaleOrc10', 'NEAT,MATURE,TIEDBACK')
    lbl('HairMaleOrc11', 'NEAT,MATURE,TIEDBACK')
    lbl('HairMaleOrc12', 'NEAT,MILITARY,TIEDBACK')
    lbl('HairMaleOrc13', 'NEAT,MILITARY,TIEDBACK')
    lbl('HairMaleOrc14', 'NEAT,MILITARY,TIEDBACK')
    lbl('HairMaleOrc15', 'MOHAWK,NEAT,MILITARY,TIEDBACK')
    lbl('HairMaleOrc16', 'MOHAWK,NEAT,MILITARY,TIEDBACK')
    lbl('HairMaleOrc17', 'MOHAWK,NEAT,MILITARY,TIEDBACK')
    lbl('HairMaleOrc18', 'SHORT,TIEDBACK,NEAT,MILITARY')
    lbl('HairMaleOrc19', 'SHORT,TIEDBACK,NEAT,MILITARY')
    lbl('HairMaleOrc20', 'SHORT,TIEDBACK,NEAT,MILITARY')
    lbl('HairMaleOrc21', 'SHORT,TIEDBACK,NEAT,MILITARY')
    lbl('HairMaleOrc22', 'SHORT,TIEDBACK,NEAT,MILITARY')
    lbl('HairMaleOrc23', 'SHORT,TIEDBACK,NEAT,MILITARY')
    lbl('HairMaleOrc24', 'SHORT,TIEDBACK,NEAT')
    lbl('HairMaleOrc25', 'BUZZ,NEAT,MILITARY')
    lbl('HairMaleOrc27', 'MOHAWK,FUNKY,BOLD')

    # Orc Female
    lbl('HairFemaleOrc01', 'SHORT,TIEDBACK,NEAT')
    lbl('HairFemaleOrc02', 'SHORT,TIEDBACK,NEAT')
    lbl('HairFemaleOrc03', 'SHORT,NEAT,TIEDBACK')
    lbl('HairFemaleOrc04', 'SHORT,NEAT,TIEDBACK')
    lbl('HairFemaleOrc05', 'BUN,MATURE,TIEDBACK,ELABORATE')
    lbl('HairFemaleOrc06', 'BUN,MATURE,TIEDBACK,ELABORATE')
    lbl('HairFemaleOrc07', 'BUN,BOLD,MATURE,TIEDBACK,ELABORATE')
    lbl('HairFemaleOrc08', 'BUN,BOLD,MATURE,TIEDBACK,ELABORATE')
    lbl('HairFemaleOrc09', 'BUZZ,NEAT,MILITARY,BOLD')
    lbl('HairFemaleOrc10', 'BUZZ,NEAT,FUNKY,BOLD')
    lbl('HairFemaleOrc11', 'BUZZ,NEAT,FUNKY,BOLD')
    lbl('HairFemaleOrc12', 'BUZZ,NEAT,FUNKY,BOLD')
    lbl('HairFemaleOrc13', 'BUZZ,NEAT,MILITARY,BOLD')
    lbl('HairFemaleOrc14', 'SHORT,BOLD,MOHAWK,TIEDBACK')
    lbl('HairFemaleOrc15', 'SHORT,BOLD,MOHAWK,TIEDBACK')
    lbl('HairFemaleOrc17', 'DREADS,FUNKY,BOLD,MOHAWK')

    # Redguard
    lbl('HairMaleRedguard1', 'BUZZ,NEAT,MILITARY')
    lbl('HairMaleRedguard2', 'BUZZ,NEAT,MILITARY')
    lbl('HairMaleRedguard3', 'SHORT,NEAT,MILITARY')
    lbl('HairMaleRedguard4', 'SHORT,NEAT,MILITARY,BOLD')
    lbl('HairMaleRedguard5', 'LONG,DREADS,MESSY')
    lbl('HairMaleRedguard6', 'SHORT,BRAIDS,NEAT,MILITARY')
    lbl('HairMaleRedguard7', 'SHORT,BRAIDS,MOHAWK,NEAT,MILITARY')
    lbl('HairMaleRedguard8', 'MOHAWK,DREADS,MESSY')
    lbl('HairFemaleRedguard01', 'TIEDBACK,NEAT')
    lbl('HairFemaleRedguard02', 'SHORT')
    lbl('HairFemaleRedguard03', 'BUZZ,MILITARY,SHORT,BOLD')
    lbl('HairFemaleRedguard04', 'BUZZ,MILITARY,SHORT,BOLD')

    # Elf Female
    lbl('HairFemaleElf01', 'MESSY')
    lbl('HairFemaleElf02', 'MESSY,BRAIDS')
    lbl('HairFemaleElf03', 'NEAT,TIEDBACK,BRAIDS,BOLD')
    lbl('HairFemaleElf04', 'NEAT,TIEDBACK')
    lbl('HairFemaleElf05', 'BRAIDS,NEAT,BOLD')
    lbl('HairFemaleElf06', 'SHORT,NEAT')
    lbl('HairFemaleElf07', 'TIEDBACK,BRAIDS,ELABORATE')
    lbl('HairFemaleElf08', 'LONG,MESSY')
    lbl('HairFemaleElf09', 'TIEDBACK,BRAIDS,ELABORATE')
    lbl('HairFemaleElf10', 'SHORT,NEAT')

    # Elf Male
    lbl('HairMaleElf01', 'NEAT,TIEDBACK')
    lbl('HairMaleElf02', 'MESSY,BRAIDS')
    lbl('HairMaleElf03', 'NEAT,TIEDBACK')
    lbl('HairMaleElf04', 'SHORT,NEAT')
    lbl('HairMaleElf05', 'BRAIDS,FUNKY,SHORT')
    lbl('HairMaleElf06', 'LONG,NEAT,TIEDBACK')
    lbl('HairMaleElf07', 'SHORT,MESSY')
    lbl('HairMaleElf08', 'LONG,MESSY')
    lbl('HairMaleElf09', 'LONG,MESSY')

    # Dark Elf Female
    lbl('HairFemaleDarkElf01', 'LONG')
    lbl('HairFemaleDarkElf02', 'SHORT,NEAT,MILITARY')
    lbl('HairFemaleDarkElf03', 'LONG,TIEDBACK,FUNKY')
    lbl('HairFemaleDarkElf04', 'LONG,TIEDBACK,FUNKY')
    lbl('HairFemaleDarkElf05', 'SHORT')
    lbl('HairFemaleDarkElf06', 'MOHAWK,NEAT,MILITARY')
    lbl('HairFemaleDarkElf07', 'BUZZ,BOLD,MILITARY')
    lbl('HairFemaleDarkElf08', 'MOHAWK,BOLD')

    # Dark Elf Male
    lbl('HairMaleDarkElf01', 'LONG,NEAT,TIEDBACK')
    lbl('HairMaleDarkElf02', 'SHORT,NEAT,TIEDBACK')
    lbl('HairMaleDarkElf03', 'LONG,TIEDBACK,FUNKY')
    lbl('HairMaleDarkElf04', 'SHORT,MOHAWK,FUNKY,BOLD')
    lbl('HairMaleDarkElf05', 'MOHAWK,BOLD')
    lbl('HairMaleDarkElf06', 'SHORT,FUNKY')
    lbl('HairMaleDarkElf07', 'BUZZ,MILITARY')
    lbl('HairMaleDarkElf08', 'SHORT,NEAT')
    lbl('HairMaleDarkElf09', 'LONG,TIEDBACK,FUNKY,BOLD')


def define_empty_headparts(ctx: RaceDefContext) -> None:
    """Mark headparts that represent empty slots."""
    for hp in [
        'BrowsFemaleArgonian00', 'BrowsMaleArgonian00',
        'BrowsMaleHumanoid12NoBrow', 'FemaleBrowsHuman12NoBrow',
        'HairArgonianFemale00', 'HairArgonianMale00', 'HairKhajiit00',
        'HumanBeard00NoBeard', 'KhajiitNoBeard',
        'MarksFemaleArgonianScar00', 'MarksFemaleHumanoid00NoGash',
        'MarksFemaleKhajiitScar00', 'MarksMaleArgonianScar00',
        'MarksMaleHumanoid00NoScar', 'MarksMaleKhajiitScar00',
    ]:
        ctx.set_empty_headpart(hp)


def define_label_conflicts(ctx: RaceDefContext) -> None:
    """Define label pairs that conflict (e.g. MILITARY NPC can't have MESSY hair)."""
    for a, b in [
        ('FUNKY', 'NOBLE'),
        ('MESSY', 'NEAT'),
        ('MESSY', 'NOBLE'),
        ('MILITARY', 'ELABORATE'),
        ('MILITARY', 'FEATHERS'),
        ('MILITARY', 'FUNKY'),
        ('MILITARY', 'MESSY'),
        ('YOUNG', 'OLD'),
        ('YOUNG', 'MATURE'),
        ('SHORT', 'LONG'),
    ]:
        ctx.label_conflict(a, b)


def define_scars(ctx: RaceDefContext) -> None:
    """Label all vanilla scar headparts with location tags."""
    lbl = ctx.label_headpart_list

    # Female scars
    lbl('MarksFemaleHumanoid01LeftGash', 'LEFT,EYE')
    lbl('MarksFemaleHumanoid02LeftGash', 'LEFT')
    lbl('MarksFemaleHumanoid03LeftGash', 'LEFT,CHEEK')
    lbl('MarksFemaleHumanoid04LeftGash', 'LEFT,CHEEK,NOSE')
    lbl('MarksFemaleHumanoid05LeftGash', 'LEFT,CHEEK')
    lbl('MarksFemaleHumanoid06LeftGash', 'LEFT,EYE,NOSE')
    lbl('MarksFemaleHumanoid07RightGash', 'RIGHT,EYE')
    lbl('MarksFemaleHumanoid08RightGash', 'RIGHT,CHEEK')
    lbl('MarksFemaleHumanoid09RightGash', 'RIGHT,CHEEK')
    lbl('MarksFemaleHumanoid10LeftGash', 'LEFT,NOSE,MOUTH')
    lbl('MarksFemaleHumanoid10RightGashR', 'LEFT,NOSE,MOUTH')
    lbl('MarksFemaleHumanoid11LeftGash', 'LEFT,NOSE,CHEEK')
    lbl('MarksFemaleHumanoid11LeftGashR', 'LEFT,NOSE,CHEEK,MOUTH')
    lbl('MarksFemaleHumanoid12LeftGash', 'CHIN,LEFT')
    lbl('MarksFemaleHumanoid12LeftGashR', 'CHIN,CHEEK')

    # Male scars
    lbl('MarksMaleHumanoid01LeftGash', 'LEFT,CHEEK,EYE')
    lbl('MarksMaleHumanoid02LeftGash', 'LEFT,CHEEK,CHIN')
    lbl('MarksMaleHumanoid03LeftGash', 'LEFT,CHEEK')
    lbl('MarksMaleHumanoid04LeftGash', 'LEFT,CHEEK,NOSE')
    lbl('MarksMaleHumanoid04RightGashR', 'LEFT,CHEEK,NOSE')
    lbl('MarksMaleHumanoid05LeftGash', 'LEFT,CHEEK,MOUTH')
    lbl('MarksMaleHumanoid06LeftGash', 'LEFT,NOSE')
    lbl('MarksMaleHumanoid06RightGashR', 'RIGHT,LEFT,NOSE')
    lbl('MarksMaleHumanoid07RightGash', 'RIGHT,EYE')
    lbl('MarksMaleHumanoid08RightGash', 'RIGHT,CHEEK')
    lbl('MarksMaleHumanoid09RightGash', 'RIGHT,CHEEK,MOUTH')
    lbl('MarksMaleHumanoid10LeftGash', 'LEFT,NOSE,MOUTH,CHIN')
    lbl('MarksMaleHumanoid10RightGashR', 'NOSE,MOUTH,CHIN')
    lbl('MarksMaleHumanoid11LeftGash', 'LEFT,CHEEK,MOUTH,NOSE')
    lbl('MarksMaleHumanoid11RightGashR', 'CHEEK,MOUTH,NOSE')
    lbl('MarksMaleHumanoid12LeftGash', 'LEFT')
    lbl('MarksMaleHumanoid12RightGashR', 'RIGHT,CHIN')


# NPC aliases: base NPC -> list of alternate EditorIDs
NPC_ALIASES = {
    'AmaundMotierre': ['AmaundMotierreEnd'],
    'Astrid': ['AstridEnd'],
    'Breya': ['BreyaCorpse'],
    'Cicero': ['CiceroDawnstar', 'CiceroRoad'],
    'Curwe': ['CurweDead'],
    'DA01MalynVaren': ['DA01MalynVarenCorpse'],
    'DA05Sinding': ['DA05SindingGhost', 'DA05SindingHuman'],
    'DBLis': ['DBLisDead'],
    'Delphine': ['Delphine3DNPC'],
    'DLC1Harkon': ['DLC1HarkonCombat'],
    'DLC1LD_Katria': ['DLC1LD_KatriaCorpse'],
    'DLC1Malkus': ['DLC1MalkusDead'],
    'DLC1VigilantTolan': ['DLC1VQ01VigilantTolanCorpse'],
    'DLC2Miraak': ['DLC2MiraakMQ01', 'DLC2MiraakMQ06'],
    'DLC2RRLygrleidSolstheim': ['DLC2RRLygrleidWindhelm'],
    'DLC2RRSogrlafSolstheim': ['DLC2RRSogrlafWindhelm'],
    'Dravynea': ['DravyneaDUPLICATE001'],
    'Drennen': ['DrennenCorpse'],
    'dunAnsilvundFemaleGhost': ['DunAnsilvundDraugrWarlordFemale'],
    'dunAnsilvundMaleGhost': ['DunAnsilvundDraugrWarlord'],
    'dunGeirmundSigdis': ['dunGeirmundSigdisDuplicate', 'dunReachwaterRockSigdisDuplicate'],
    'Eltrys': ['EltrysDead'],
    'FelldirTheOld': ['MQ206Felldir', 'SummonFelldir'],
    'FestusKrex': ['FestusKrexDead'],
    'Gabriella': ['GabriellaDead'],
    'Galmar': ['CWBattleGalmar'],
    'GeneralTullius': ['CWBattleTullius'],
    'GormlaithGoldenHilt': ['MQ206Gormlaith', 'SummonGormlaith'],
    'Haming': ['dunHunterChild'],
    'Kodlak': ['C04DeadKodlak', 'C06DeadKodlak', 'MQ304Kodlak'],
    'Malborn': ['MQ201FakeMalborn'],
    'MQ206Hakon': ['HakonOneEye', 'SummonHakon'],
    'MS13Arvel': ['e3DemoArvel'],
    'Nazir': ['NazirSancAttack'],
    'Nerien': ['MG02Nerien'],
    'Rikke': ['CWBattleRikke'],
    'SavosAren': ['SavosArenGhost'],
    'Susanna': ['MS11SusannaDeadA'],
    'Thorek': ['Thorek_Ambush'],
    'TitusMedeII': ['TitusMedeIIDecoy'],
    'Tova': ['TovaDead'],
    'Ulfric': ['CWBattleUlfric', 'MQ304Ulfric'],
    'VantusLoreius': ['VantusLoreiusDead'],
    'Veezara': ['VeezaraDead'],
    'VerenDuleri': ['VerenDuleri_Ambush'],
    'WatchesTheRoots': ['WatchesTheRootsCorpse'],
    'WEDL04PlautisCarvain': ['WEDL03PlautisCarvain'],
    'WEDL04SaloniaCarvain': ['WEDL03SaloniaCarvain'],
}

# Build reverse lookup: alternate EditorID -> base EditorID
_ALIAS_REVERSE = {}
for base, alts in NPC_ALIASES.items():
    for alt in alts:
        _ALIAS_REVERSE[alt] = base


def unalias(editor_id: str) -> str:
    """Resolve an NPC alias to its base EditorID."""
    return _ALIAS_REVERSE.get(editor_id, editor_id)


# NPCs with forced race assignments
NPC_RACE_OVERRIDES = {
    'Ainethach': 'YASReachmanRace',
    'Belchimac': 'YASReachmanRace',
    'Cosnach': 'YASReachmanRace',
    'Duach': 'YASReachmanRace',
    'Enmon': 'YASReachmanRace',
    'Gralnach': 'YASReachmanRaceChild',
    'Mena': 'YASReachmanRace',
    'Rondach': 'YASReachmanRace',
    'MS01Weylin': 'YASReachmanRace',
    'Anton': 'YASReachmanRace',
    'Hathrasil': 'YASReachmanRace',
    'Omluag': 'YASReachmanRace',
    'Madanach': 'YASReachmanRace',
    'NeposTheNose': 'YASReachmanRace',
    'DLC2RRCresciusCaerellius': 'NordRace',
    'SeptimusSignus': 'ImperialRace',
    'BrotherVerulus': 'ImperialRace',
    'KeeperCarcette': 'BretonRace',
    'EncOrcWarriorOld': 'OrcRace',
}


def define_npc_aliases(ctx: RaceDefContext) -> None:
    """Register NPC aliases on the context (for use during NPC processing)."""
    # Aliases are stored as module-level data (NPC_ALIASES dict)
    # and resolved via unalias(). No need to register on ctx.
    pass


def assign_npc_races(ctx: RaceDefContext) -> None:
    """Register forced NPC race assignments."""
    for npc_id, race_id in NPC_RACE_OVERRIDES.items():
        ctx.set_npc_race(npc_id, race_id)
