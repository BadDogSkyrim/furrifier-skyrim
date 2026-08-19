"""`--armor` and `--schlongs` partition the armor space on biped slot 52.

Unit coverage for the two pure helpers behind the split. The behaviour
they drive is checked against real plugins in
tests/integration/test_armor_slot_split.py -- these tests exist for the
edge cases a real load order does not conveniently contain, notably an
ARMO whose addons are all non-furrifiable.
"""

import pytest

from furrifier.context import (
    ARMOR_ONLY_BODYPARTS, FURRIFIABLE_BODYPARTS, SCHLONG_BODYPARTS,
    armo_in_bodypart_scope, armor_bodypart_mask,
)
from furrifier.models import Bodypart


# -- Stubs --
#
# get_bodypart_flags only ever asks an ARMA for its BOD2/BODT subrecord,
# so a two-attribute stand-in is the whole surface needed here.

class _Bod2:
    def __init__(self, value):
        self._value = value
        self.size = 4

    def get_uint32(self, offset=0):
        return self._value


class _FakeArma:
    def __init__(self, flags):
        self._flags = int(flags)

    def get_subrecord(self, signature):
        return _Bod2(self._flags) if signature == 'BOD2' else None


HELMET = _FakeArma(Bodypart.HEAD | Bodypart.HAIR)
SHEATH = _FakeArma(Bodypart.SCHLONG)
CUIRASS = _FakeArma(0x4)          # slot 32, body -- not furrifiable
CIRCLET = _FakeArma(Bodypart.CIRCLET)

ARMAS = {1: HELMET, 2: SHEATH, 3: CUIRASS, 4: CIRCLET}

MASK_ARMOR = armor_bodypart_mask(True, False)
MASK_SCHLONG = armor_bodypart_mask(False, True)
MASK_BOTH = armor_bodypart_mask(True, True)


class TestArmorBodypartMask:

    def test_both_flags_reproduce_the_unsplit_mask(self):
        """The default path must be untouched by the split: a run that
        asks for both halves has to see exactly what every caller saw
        before the mask parameter existed."""
        assert MASK_BOTH == int(FURRIFIABLE_BODYPARTS)

    def test_armor_alone_excludes_slot_52(self):
        assert MASK_ARMOR == int(ARMOR_ONLY_BODYPARTS)
        assert not MASK_ARMOR & Bodypart.SCHLONG

    def test_schlongs_alone_is_only_slot_52(self):
        assert MASK_SCHLONG == int(SCHLONG_BODYPARTS)
        assert MASK_SCHLONG & Bodypart.SCHLONG

    def test_neither_flag_is_an_empty_mask(self):
        """main.py reads 0 as 'skip the armor pass'. A zero mask matches
        no addon, so the two readings agree -- but the caller should not
        have to run the pass to find that out."""
        assert armor_bodypart_mask(False, False) == 0

    def test_the_two_halves_are_disjoint_and_complete(self):
        assert not MASK_ARMOR & MASK_SCHLONG
        assert MASK_ARMOR | MASK_SCHLONG == int(FURRIFIABLE_BODYPARTS)


class TestArmoInBodypartScope:

    @pytest.mark.parametrize("mask", [MASK_ARMOR, MASK_SCHLONG, MASK_BOTH])
    def test_no_furrifiable_addons_is_always_in_scope(self, mask):
        """Body-only armor stays in scope under every mask. Merging it is
        load-order conflict resolution, which the furrifier has always
        done and which has nothing to do with slot 52 -- skipping it
        would resurrect armor conflicts, not shrink a patch usefully."""
        assert armo_in_bodypart_scope([3], ARMAS, mask)

    def test_unresolvable_addons_read_as_no_furrifiable_addons(self):
        """An addon the load order cannot resolve cannot be classified,
        so it must not be the reason an ARMO is dropped."""
        assert armo_in_bodypart_scope([99], ARMAS, MASK_SCHLONG)

    def test_empty_addon_list_is_in_scope(self):
        assert armo_in_bodypart_scope([], ARMAS, MASK_SCHLONG)

    def test_sheath_only_armo_is_out_of_scope_for_armor(self):
        assert not armo_in_bodypart_scope([2], ARMAS, MASK_ARMOR)

    def test_sheath_only_armo_is_in_scope_for_schlongs(self):
        assert armo_in_bodypart_scope([2], ARMAS, MASK_SCHLONG)

    def test_helmet_only_armo_is_out_of_scope_for_schlongs(self):
        assert not armo_in_bodypart_scope([1], ARMAS, MASK_SCHLONG)

    def test_helmet_only_armo_is_in_scope_for_armor(self):
        assert armo_in_bodypart_scope([1], ARMAS, MASK_ARMOR)

    def test_mixed_armo_is_in_scope_for_either_half(self):
        """One matching addon is enough. An ARMO listing both a helmet
        and a sheath has work to do in both runs; the mask then decides
        which of its addons the furrification pass can see."""
        assert armo_in_bodypart_scope([1, 2], ARMAS, MASK_ARMOR)
        assert armo_in_bodypart_scope([1, 2], ARMAS, MASK_SCHLONG)

    def test_body_addon_does_not_rescue_an_out_of_scope_armo(self):
        """A non-furrifiable addon alongside an out-of-scope furrifiable
        one must not flip the verdict -- otherwise nearly every sheath
        ARMO would survive an --armor run on the strength of its body
        addon."""
        assert not armo_in_bodypart_scope([2, 3], ARMAS, MASK_ARMOR)

    @pytest.mark.parametrize("keys", [[1], [2], [3], [1, 2], [2, 3], []])
    def test_full_mask_keeps_everything(self, keys):
        assert armo_in_bodypart_scope(keys, ARMAS, MASK_BOTH)
