"""Tests for headpart matching and label logic."""

import pytest
from furrifier.headparts import (
    labels_conflict, add_label_no_conflict, calculate_label_match_score,
    find_best_headpart_match,
)
from furrifier.models import Sex, HeadpartType, HeadpartInfo
from furrifier.race_defs import RaceDefContext


@pytest.fixture
def ctx():
    """Context with standard label conflicts."""
    c = RaceDefContext()
    c.label_conflict('SHORT', 'LONG')
    c.label_conflict('MESSY', 'NEAT')
    c.label_conflict('MESSY', 'NOBLE')
    c.label_conflict('MILITARY', 'MESSY')
    c.label_conflict('MILITARY', 'FUNKY')
    c.label_conflict('YOUNG', 'OLD')
    return c


class TestLabelsConflict:
    def test_conflict(self, ctx):
        assert labels_conflict('SHORT', 'LONG', ctx)

    def test_no_conflict(self, ctx):
        assert not labels_conflict('SHORT', 'NEAT', ctx)

    def test_symmetric(self, ctx):
        assert labels_conflict('LONG', 'SHORT', ctx)

    def test_same_label(self, ctx):
        assert not labels_conflict('SHORT', 'SHORT', ctx)


class TestAddLabelNoConflict:
    def test_adds_non_conflicting(self, ctx):
        labels = ['SHORT']
        add_label_no_conflict(labels, 'NEAT', ctx)
        assert 'NEAT' in labels

    def test_blocks_conflicting(self, ctx):
        labels = ['SHORT']
        add_label_no_conflict(labels, 'LONG', ctx)
        assert 'LONG' not in labels

    def test_empty_list(self, ctx):
        labels = []
        add_label_no_conflict(labels, 'ANYTHING', ctx)
        assert labels == ['ANYTHING']


class TestLabelMatchScore:
    def test_perfect_match(self, ctx):
        score = calculate_label_match_score(
            ['SHORT', 'NEAT'], ['SHORT', 'NEAT'], ctx)
        assert score == 2

    def test_no_match(self, ctx):
        score = calculate_label_match_score(
            ['SHORT'], ['BRAIDS'], ctx)
        assert score == -1

    def test_conflict_penalty(self, ctx):
        score = calculate_label_match_score(
            ['SHORT'], ['LONG'], ctx)
        assert score == -1000

    def test_partial_match(self, ctx):
        score = calculate_label_match_score(
            ['SHORT', 'NEAT', 'MILITARY'], ['SHORT', 'NEAT'], ctx)
        # SHORT: +1, NEAT: +1, MILITARY: -1 (not in hp labels)
        assert score == 1

    def test_empty_labels(self, ctx):
        score = calculate_label_match_score([], ['SHORT'], ctx)
        assert score == 0


class TestFindBestHeadpartMatch:
    def _make_hp(self, edid, labels=None, equivalents=None):
        return HeadpartInfo(
            record=None, editor_id=edid, hp_type=HeadpartType.HAIR,
            labels=labels or [], equivalents=equivalents or [],
        )

    def test_label_matching(self, ctx):
        old_hp = self._make_hp('VanillaHair')
        furry1 = self._make_hp('FurryHairNeat', labels=['SHORT', 'NEAT'])
        furry2 = self._make_hp('FurryHairMessy', labels=['LONG', 'MESSY'])

        all_headparts = {
            'FurryHairNeat': furry1,
            'FurryHairMessy': furry2,
        }
        race_headparts = {
            (HeadpartType.HAIR, 0, 'FurryRace'): {'FurryHairNeat', 'FurryHairMessy'},
        }

        result = find_best_headpart_match(
            old_hp, 'TestNPC', Sex.MALE_ADULT, ['SHORT', 'NEAT'],
            'FurryRace', race_headparts, all_headparts, ctx,
        )
        assert result == furry1

    def test_conflict_avoidance(self, ctx):
        old_hp = self._make_hp('VanillaHair')
        furry1 = self._make_hp('FurryHairLong', labels=['LONG'])
        furry2 = self._make_hp('FurryHairShort', labels=['SHORT'])

        all_headparts = {'FurryHairLong': furry1, 'FurryHairShort': furry2}
        race_headparts = {
            (HeadpartType.HAIR, 0, 'FurryRace'): {'FurryHairLong', 'FurryHairShort'},
        }

        # NPC has SHORT label, LONG conflicts with SHORT
        result = find_best_headpart_match(
            old_hp, 'TestNPC', Sex.MALE_ADULT, ['SHORT'],
            'FurryRace', race_headparts, all_headparts, ctx,
        )
        assert result == furry2

    def test_equivalents_override_labels(self, ctx):
        old_hp = self._make_hp('VanillaHair', equivalents=['SpecificFurryHair'])
        specific = self._make_hp('SpecificFurryHair')

        all_headparts = {'SpecificFurryHair': specific}
        race_headparts = {
            (HeadpartType.HAIR, 0, 'FurryRace'): {'SpecificFurryHair'},
        }

        result = find_best_headpart_match(
            old_hp, 'TestNPC', Sex.MALE_ADULT, [],
            'FurryRace', race_headparts, all_headparts, ctx,
        )
        assert result == specific

    def test_no_available_headparts(self, ctx):
        old_hp = self._make_hp('VanillaHair')
        result = find_best_headpart_match(
            old_hp, 'TestNPC', Sex.MALE_ADULT, [],
            'FurryRace', {}, {}, ctx,
        )
        assert result is None

    def test_deterministic(self, ctx):
        """Same NPC always gets same headpart."""
        old_hp = self._make_hp('VanillaHair')
        hps = {f'Hair{i}': self._make_hp(f'Hair{i}') for i in range(10)}
        race_hps = {
            (HeadpartType.HAIR, 0, 'FurryRace'): set(hps.keys()),
        }

        r1 = find_best_headpart_match(
            old_hp, 'Lydia', Sex.FEMALE_ADULT, [],
            'FurryRace', race_hps, hps, ctx)
        r2 = find_best_headpart_match(
            old_hp, 'Lydia', Sex.FEMALE_ADULT, [],
            'FurryRace', race_hps, hps, ctx)
        assert r1 is r2
