"""Tests for race definitions and preference schemes."""

from pathlib import Path

import pytest
from furrifier.race_defs import (
    _parse_leveled_npcs, load_scheme, RaceDefContext, SCHEMES,
)


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


    def test_headpart_probability_wildcard_race(self):
        ctx = RaceDefContext()
        ctx.set_headpart_probability('*', None, 'EYEBROWS', 0.3)
        # Any unlisted race picks up the wildcard default.
        assert ctx.get_headpart_probability(
            'YASRandomRace', 'Male', 'EYEBROWS') == 0.3
        assert ctx.get_headpart_probability(
            'SomeOtherRace', 'Female', 'EYEBROWS') == 0.3


    def test_headpart_probability_specific_overrides_wildcard(self):
        ctx = RaceDefContext()
        ctx.set_headpart_probability('*', None, 'EYEBROWS', 0.3)
        ctx.set_headpart_probability('YASMinoRace', 'Male', 'EYEBROWS', 1.0)
        # Mino male gets the specific entry, not the wildcard.
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Male', 'EYEBROWS') == 1.0
        # Mino female still falls through to wildcard (no specific entry).
        assert ctx.get_headpart_probability(
            'YASMinoRace', 'Female', 'EYEBROWS') == 0.3


    def test_yas_races_probability_loaded(self):
        """yas_races.toml headpart_probability entries land in every
        scheme's context (catalog is scheme-independent)."""
        ctx = load_scheme('all_races_test')
        # Mino male brows = 1.0, Mino female brows = 0.5 per yas_races.toml.
        assert ctx.get_headpart_probability(
            'BDMinoRace', 'Male', 'EYEBROWS') == 1.0
        assert ctx.get_headpart_probability(
            'BDMinoRace', 'Female', 'EYEBROWS') == 0.5
        # Deer male facial hair = 0.2.
        assert ctx.get_headpart_probability(
            'BDDeerRace', 'Male', 'FACIAL_HAIR') == 0.2


class TestLeveledNpcsParsing:
    """Validation warnings for the [leveled_npcs] scheme section."""

    PATH = Path('test.toml')

    def _parse(self, section):
        ctx = RaceDefContext()
        _parse_leveled_npcs({'leveled_npcs': section}, ctx, self.PATH)
        return ctx

    def test_obsolete_top_level_races_warns(self, caplog):
        """Old format with ``races =`` at the leveled_npcs root must
        produce a warning, not silent zero overrides."""
        with caplog.at_level('WARNING'):
            ctx = self._parse({
                'races': [{'race': 'YASLykaiosRace', 'probability': 0.1}],
            })
        assert ctx.leveled_npc_groups == []
        assert any('obsolete' in r.message.lower() for r in caplog.records)

    def test_unknown_top_level_key_warns(self, caplog):
        with caplog.at_level('WARNING'):
            self._parse({'bogus': 1, 'groups': []})
        assert any("'bogus'" in r.message for r in caplog.records)

    def test_unknown_group_key_warns(self, caplog):
        with caplog.at_level('WARNING'):
            self._parse({
                'groups': [
                    {'match_substring': ['bandit'], 'races': []},  # typo
                ],
            })
        assert any("'match_substring'" in r.message for r in caplog.records)

    def test_missing_race_key_warns_and_skips(self, caplog):
        with caplog.at_level('WARNING'):
            ctx = self._parse({
                'groups': [
                    {'races': [
                        {'probability': 0.1},
                        {'race': 'YASLykaiosRace', 'probability': 0.1},
                    ]},
                ],
            })
        assert any("missing required 'race'" in r.message
                   for r in caplog.records)
        # Second rule still parsed.
        assert len(ctx.leveled_npc_groups[0].races) == 1

    def test_clean_config_no_warnings(self, caplog):
        with caplog.at_level('WARNING'):
            ctx = self._parse({
                'exclude_substrings': ['Thalmor'],
                'groups': [
                    {'match_substrings': ['bandit'], 'races': [
                        {'race': 'YASLykaiosRace', 'probability': 0.1},
                    ]},
                ],
            })
        assert not [r for r in caplog.records
                    if r.name.startswith('furrifier')]
        assert len(ctx.leveled_npc_groups) == 1
        assert ctx.leveled_npc_groups[0].races[0].race == 'YASLykaiosRace'
