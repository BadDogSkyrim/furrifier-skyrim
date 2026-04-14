"""Tests for headpart matching and label logic."""

import pytest
from furrifier.furry_load import build_race_headparts
from furrifier.headparts import (
    labels_conflict, add_label_no_conflict, calculate_label_match_score,
    find_best_headpart_match, _blindness_state,
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


class _StubRecord:
    """Minimal Record stub — get_subrecord returns None so the loop
    exits after the EXCLUDE check without needing real DATA/RNAM."""
    def get_subrecord(self, sig):
        return None


class TestBuildRaceHeadpartsExclude:
    """EXCLUDE label filters headparts out of the candidate index."""


    def test_exclude_headpart_is_skipped(self):
        excluded = HeadpartInfo(
            record=_StubRecord(), editor_id='BDDeerFemMane',
            hp_type=HeadpartType.HAIR, labels=['EXCLUDE'],
        )
        all_headparts = {'BDDeerFemMane': excluded}
        result = build_race_headparts([], all_headparts)
        assert result == {}


class TestBlindnessState:
    """_blindness_state parses headpart EditorIDs."""

    def test_none_for_plain_eye(self):
        assert _blindness_state('MaleEyesHumanBrown') == 'none'

    def test_none_for_bloodshot(self):
        assert _blindness_state('MaleEyesHumanBrownBloodShot') == 'none'

    def test_none_for_empty(self):
        assert _blindness_state('') == 'none'
        assert _blindness_state(None) == 'none'

    def test_full_blind_at_end(self):
        assert _blindness_state('MaleEyesHumanBlind') == 'full'
        assert _blindness_state('YASNightPredMaleEyesBlind') == 'full'

    def test_blind_left_full_word(self):
        assert _blindness_state('MaleEyesHumanBrownBlindLeft') == 'left'
        assert _blindness_state('YASNightPredMaleEyesOrangeBlindLeft') == 'left'

    def test_blind_right_full_word(self):
        assert _blindness_state('MaleEyesHumanBrownBlindRight') == 'right'
        assert _blindness_state('MaleEyesHighElfYellowBlindRight') == 'right'

    def test_blind_l_abbrev_at_end(self):
        assert _blindness_state('YASNightPredMaleEyesAmberBlindL') == 'left'
        assert _blindness_state('YASDayPredMaleEyesBlueGreyBlindL') == 'left'

    def test_blind_r_abbrev_at_end(self):
        assert _blindness_state('YASNightPredMaleEyesAmberBlindR') == 'right'
        assert _blindness_state('YASNightPredMaleEyesYellowBlindR') == 'right'

    def test_blind_l_camelcase_middle(self):
        # YASCatDayMaleEyesBlindLAmber — BlindL in the middle, camelCase
        assert _blindness_state('YASCatDayMaleEyesBlindLAmber') == 'left'

    def test_blinded_is_not_blind(self):
        # 'Blinded' has 'e' after Blind — should not match
        assert _blindness_state('BlindedFoo') == 'none'


class TestFindBestHeadpartMatch_Blindness:
    """Blindness filtering in find_best_headpart_match for EYES."""

    def _make_eye(self, edid, labels=None, equivalents=None):
        return HeadpartInfo(
            record=None, editor_id=edid, hp_type=HeadpartType.EYES,
            labels=labels or [], equivalents=equivalents or [],
        )

    def _run(self, old_eye, available_edids, all_eyes, ctx_fixture):
        race_hps = {
            (HeadpartType.EYES, 0, 'FurryRace'): set(available_edids),
        }
        return find_best_headpart_match(
            old_eye, 'TestNPC', Sex.MALE_ADULT, [],
            'FurryRace', race_hps, all_eyes, ctx_fixture,
        )

    def test_sighted_vanilla_rejects_blind_fallback(self, ctx):
        """Non-blind vanilla eye must not land on a blind furry eye
        via the label-scoring fallback."""
        old = self._make_eye('MaleEyesHumanHazelBrown')
        blind = self._make_eye('YASNightPredMaleEyesBlind')
        sighted = self._make_eye('YASNightPredMaleEyesAmber')
        all_eyes = {blind.editor_id: blind, sighted.editor_id: sighted}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is sighted, \
            f"Sighted NPC must get sighted eye, got {result.editor_id}"

    def test_sighted_vanilla_returns_none_if_only_blind_available(self, ctx):
        """If only blind eyes are available, a sighted NPC gets nothing
        (better than wrongly becoming blind)."""
        old = self._make_eye('MaleEyesHumanBrown')
        blind = self._make_eye('YASNightPredMaleEyesBlind')
        all_eyes = {blind.editor_id: blind}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is None

    def test_blind_right_vanilla_prefers_blind_right_furry(self, ctx):
        """Half-blind right vanilla → half-blind right furry when available."""
        old = self._make_eye('MaleEyesHighElfYellowBlindRight')
        blind_l = self._make_eye('YASCatDayMaleEyesBlindLAmber')
        blind_r = self._make_eye('YASCatDayMaleEyesBlindRAmber')
        sighted = self._make_eye('YASCatDayMaleEyesAmber')
        all_eyes = {e.editor_id: e for e in (blind_l, blind_r, sighted)}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is blind_r

    def test_blind_right_vanilla_falls_back_to_blind_left(self, ctx):
        """Half-blind right vanilla with no BlindR → BlindL (other side)."""
        old = self._make_eye('MaleEyesHighElfYellowBlindRight')
        blind_l = self._make_eye('YASCatDayMaleEyesBlindLAmber')
        sighted = self._make_eye('YASCatDayMaleEyesAmber')
        full_blind = self._make_eye('YASCatMaleEyesBlind')
        all_eyes = {e.editor_id: e for e in (blind_l, sighted, full_blind)}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is blind_l, \
            "Half-blind must fall back to the other side, not full-blind"

    def test_blind_right_vanilla_falls_back_to_sighted_not_full_blind(self, ctx):
        """Half-blind vanilla with no half-blind variants → sighted,
        explicitly NOT full-blind (per spec)."""
        old = self._make_eye('MaleEyesHighElfYellowBlindRight')
        sighted = self._make_eye('YASCatDayMaleEyesAmber')
        full_blind = self._make_eye('YASCatMaleEyesBlind')
        all_eyes = {e.editor_id: e for e in (sighted, full_blind)}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is sighted, \
            "No half-blind available — should pick sighted over full-blind"

    def test_full_blind_vanilla_prefers_full_blind(self, ctx):
        old = self._make_eye('MaleEyesHumanBlind')
        full_blind = self._make_eye('YASNightPredMaleEyesBlind')
        blind_l = self._make_eye('YASNightPredMaleEyesAmberBlindL')
        sighted = self._make_eye('YASNightPredMaleEyesAmber')
        all_eyes = {e.editor_id: e for e in (full_blind, blind_l, sighted)}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is full_blind

    def test_equivalents_also_filtered_by_blindness(self, ctx):
        """If an explicit equivalent is blind but vanilla is sighted,
        the equivalent is rejected (and we fall through to label match)."""
        old = self._make_eye('MaleEyesHumanBrown',
                             equivalents=['YASNightPredMaleEyesBlind'])
        blind = self._make_eye('YASNightPredMaleEyesBlind')
        sighted = self._make_eye('YASNightPredMaleEyesBrown')
        all_eyes = {blind.editor_id: blind, sighted.editor_id: sighted}

        result = self._run(old, all_eyes.keys(), all_eyes, ctx)
        assert result is sighted, \
            "Equivalent that violates blindness state must be rejected"
