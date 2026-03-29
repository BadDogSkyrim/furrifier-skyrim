"""Tests for race definitions and preference schemes."""

import pytest
from furrifier.race_defs import load_scheme, RaceDefContext, SCHEMES


class TestLoadScheme:
    def test_all_schemes_load(self):
        """Every registered scheme loads without error."""
        for name in SCHEMES:
            ctx = load_scheme(name)
            assert len(ctx.assignments) > 0

    def test_unknown_scheme_raises(self):
        with pytest.raises(ValueError):
            load_scheme('nonexistent')

    def test_all_races_has_nords(self):
        ctx = load_scheme('all_races')
        assert 'NordRace' in ctx.assignments
        assert ctx.assignments['NordRace'].furry_id == 'YASLykaiosRace'
        assert ctx.assignments['NordRace'].furry_class == 'DOG'

    def test_all_races_has_elves(self):
        ctx = load_scheme('all_races')
        assert 'HighElfRace' in ctx.assignments
        assert ctx.assignments['HighElfRace'].furry_class == 'CAT'

    def test_all_races_has_reachman(self):
        ctx = load_scheme('all_races')
        assert 'YASReachmanRace' in ctx.subraces
        sub = ctx.subraces['YASReachmanRace']
        assert sub.vanilla_basis == 'BretonRace'
        assert sub.furry_id == 'YASKonoiRace'

    def test_all_races_has_faction_overrides(self):
        ctx = load_scheme('all_races')
        assert ctx.faction_races['ForswornFaction'] == 'YASReachmanRace'
        assert ctx.faction_races['DLC2SkaalVillageCitizenFaction'] == 'YASSkaalRace'

    def test_all_races_has_sailor_npc(self):
        ctx = load_scheme('all_races')
        assert ctx.npc_races['Jolf'] == 'YASSailorRace'

    def test_legacy_differs_from_all_races(self):
        all_ctx = load_scheme('all_races')
        leg_ctx = load_scheme('legacy')
        # Imperial -> Vaalsark in legacy, Kettu in all_races
        assert all_ctx.assignments['ImperialRace'].furry_id == 'YASKettuRace'
        assert leg_ctx.assignments['ImperialRace'].furry_id == 'YASVaalsarkRace'

    def test_cats_dogs_no_sailors(self):
        ctx = load_scheme('cats_dogs')
        assert 'YASSailorRace' not in ctx.subraces


class TestRaceDefContext:
    def test_label_headpart_list(self):
        ctx = RaceDefContext()
        ctx.label_headpart_list('TestHair', 'LONG,MESSY,BOLD')
        assert ctx.headpart_labels['TestHair'] == ['LONG', 'MESSY', 'BOLD']

    def test_label_conflict(self):
        ctx = RaceDefContext()
        ctx.label_conflict('SHORT', 'LONG')
        assert frozenset({'SHORT', 'LONG'}) in ctx.label_conflicts

    def test_assign_headpart(self):
        ctx = RaceDefContext()
        ctx.assign_headpart('VanillaHair01', 'FurryHair01')
        ctx.assign_headpart('VanillaHair01', 'FurryHair02')
        assert ctx.headpart_equivalents['VanillaHair01'] == ['FurryHair01', 'FurryHair02']

    def test_set_empty_headpart(self):
        ctx = RaceDefContext()
        ctx.set_empty_headpart('EmptyHair')
        assert 'EmptyHair' in ctx.empty_headparts
