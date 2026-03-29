"""Tests for setup and data loading.

Synthetic tests that don't require game files, plus gamefiles-marked
tests that load from Skyrim.esm.
"""

import struct
import pytest

from esplib import Plugin, Record, SubRecord, FormID

from furrifier.furry_load import is_npc_female, is_child_race, get_headpart_type
from furrifier.models import HeadpartType
from furrifier.vanilla_setup import unalias, NPC_ALIASES, NPC_RACE_OVERRIDES


class TestNPCHelpers:
    def test_is_female(self):
        npc = Record('NPC_', FormID(0x100), 0)
        npc.add_subrecord('ACBS', struct.pack('<I', 1) + b'\x00' * 20)  # bit 0 = Female
        assert is_npc_female(npc) is True

    def test_is_male(self):
        npc = Record('NPC_', FormID(0x100), 0)
        npc.add_subrecord('ACBS', struct.pack('<I', 0) + b'\x00' * 20)
        assert is_npc_female(npc) is False

    def test_no_acbs(self):
        npc = Record('NPC_', FormID(0x100), 0)
        assert is_npc_female(npc) is False


class TestHeadpartType:
    def test_hair(self):
        hdpt = Record('HDPT', FormID(0x100), 0)
        hdpt.add_subrecord('PNAM', struct.pack('<I', 3))  # xEdit Hair = 3
        assert get_headpart_type(hdpt) == HeadpartType.HAIR

    def test_eyes(self):
        hdpt = Record('HDPT', FormID(0x100), 0)
        hdpt.add_subrecord('PNAM', struct.pack('<I', 2))
        assert get_headpart_type(hdpt) == HeadpartType.EYES

    def test_scar(self):
        hdpt = Record('HDPT', FormID(0x100), 0)
        hdpt.add_subrecord('PNAM', struct.pack('<I', 5))
        assert get_headpart_type(hdpt) == HeadpartType.SCAR

    def test_no_pnam(self):
        hdpt = Record('HDPT', FormID(0x100), 0)
        assert get_headpart_type(hdpt) == HeadpartType.UNKNOWN


class TestAliases:
    def test_known_alias(self):
        assert unalias('AstridEnd') == 'Astrid'

    def test_unknown_passes_through(self):
        assert unalias('Lydia') == 'Lydia'

    def test_multiple_aliases(self):
        assert unalias('CiceroDawnstar') == 'Cicero'
        assert unalias('CiceroRoad') == 'Cicero'

    def test_alias_dict_complete(self):
        """Every alias value has a corresponding base in the dict."""
        for base, alts in NPC_ALIASES.items():
            assert isinstance(alts, list)
            for alt in alts:
                assert unalias(alt) == base


class TestNPCRaceOverrides:
    def test_reachmen(self):
        assert NPC_RACE_OVERRIDES['Madanach'] == 'YASReachmanRace'

    def test_correction(self):
        # Septimus is Imperial despite being in Skyrim.esm as Nord
        assert NPC_RACE_OVERRIDES['SeptimusSignus'] == 'ImperialRace'
