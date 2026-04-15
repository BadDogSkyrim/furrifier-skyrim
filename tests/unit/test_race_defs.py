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

    def test_all_races_has_elves(self):
        ctx = load_scheme('all_races')
        assert 'HighElfRace' in ctx.assignments
        assert ctx.assignments['HighElfRace'].furry_id == 'YASMahaRace'

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

    def test_race_catalog_loaded_into_ctx(self):
        """Regression: every scheme gets headpart equivalents and labels
        from races/*.toml, not only the scheme file itself. A previous
        pass had a TOML-scoping bug that silently swallowed headpart_equivalents
        into [npc_races]; this test would have caught it."""
        for scheme in SCHEMES:
            ctx = load_scheme(scheme)
            assert len(ctx.headpart_equivalents) > 0, (
                f"scheme {scheme!r} loaded with no headpart_equivalents — "
                f"races/*.toml catalog data not being merged"
            )
            assert len(ctx.headpart_labels) > 0, (
                f"scheme {scheme!r} loaded with no headpart_labels"
            )
            # Spot-check a known entry from races/yas_races.toml.
            assert 'MaleEyesHumanAmber' in ctx.headpart_equivalents
            assert 'YASDayPredMaleEyesAmber' in ctx.headpart_equivalents['MaleEyesHumanAmber']


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


    def test_headpart_probability_sex_specific(self):
        ctx = RaceDefContext()
        ctx.set_headpart_probability('YASMinoRace', 'Male', 'EYEBROWS', 1.0)
        ctx.set_headpart_probability('YASMinoRace', 'Female', 'EYEBROWS', 0.5)
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Male', 'EYEBROWS') == 1.0
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Female', 'EYEBROWS') == 0.5


    def test_headpart_probability_default_one(self):
        ctx = RaceDefContext()
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Male', 'EYEBROWS') == 1.0


    def test_headpart_probability_sex_agnostic_fallback(self):
        ctx = RaceDefContext()
        ctx.set_headpart_probability('BDDeerRace', None, 'FACIAL_HAIR', 0.2)
        # Both sexes fall through to the None entry.
        assert ctx.get_headpart_probability(
            'BDDeerRace', 'Male', 'FACIAL_HAIR') == 0.2
        assert ctx.get_headpart_probability(
            'BDDeerRace', 'Female', 'FACIAL_HAIR') == 0.2


    def test_yas_races_probability_loaded(self):
        """yas_races.toml headpart_probability entries land in every
        scheme's context (catalog is scheme-independent)."""
        ctx = load_scheme('all_races_test')
        # Mino male brows = 1.0, Mino female brows = 0.5 per yas_races.toml.
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Male', 'EYEBROWS') == 1.0
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Female', 'EYEBROWS') == 0.5
        # Deer male facial hair = 0.2.
        assert ctx.get_headpart_probability(
            'BDDeerRace', 'Male', 'FACIAL_HAIR') == 0.2
