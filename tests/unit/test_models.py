"""Tests for data models."""

from furrifier.models import Sex, HeadpartType, TintLayer, Bodypart


class TestSex:
    def test_is_female(self):
        assert not Sex.MALE_ADULT.is_female
        assert Sex.FEMALE_ADULT.is_female
        assert not Sex.MALE_CHILD.is_female
        assert Sex.FEMALE_CHILD.is_female

    def test_is_child(self):
        assert not Sex.MALE_ADULT.is_child
        assert not Sex.FEMALE_ADULT.is_child
        assert Sex.MALE_CHILD.is_child
        assert Sex.FEMALE_CHILD.is_child

    def test_from_flags(self):
        assert Sex.from_flags(female=False, child=False) == Sex.MALE_ADULT
        assert Sex.from_flags(female=True, child=False) == Sex.FEMALE_ADULT
        assert Sex.from_flags(female=False, child=True) == Sex.MALE_CHILD
        assert Sex.from_flags(female=True, child=True) == Sex.FEMALE_CHILD


class TestHeadpartType:
    def test_values(self):
        assert HeadpartType.MISC == 0
        assert HeadpartType.FACE == 1
        assert HeadpartType.EYES == 2
        assert HeadpartType.HAIR == 3
        assert HeadpartType.FACIAL_HAIR == 4
        assert HeadpartType.SCAR == 5
        assert HeadpartType.EYEBROWS == 6


class TestTintLayer:
    def test_skin_tone_is_zero(self):
        assert TintLayer.SKIN_TONE == 0

    def test_decoration_boundary(self):
        assert TintLayer.DECORATION_LO == 19
        assert TintLayer.BLACKBLOOD == 19


class TestBodypart:
    def test_flags(self):
        assert Bodypart.HEAD == 1
        assert Bodypart.HAIR == 2
        assert Bodypart.SCHLONG == 0x400000

    def test_combined(self):
        flags = Bodypart.HEAD | Bodypart.HAIR
        assert Bodypart.HEAD in flags
        assert Bodypart.HAIR in flags
        assert Bodypart.HANDS not in flags
