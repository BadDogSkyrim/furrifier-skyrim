"""Tests for NPC helper utilities."""

import struct
import pytest

from esplib import Record, FormID
from esplib.defs.game import GameRegistry

from furrifier.npc import determine_npc_sex
from furrifier.models import Sex

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas


def _make_npc(form_id=0x100, female=False):
    npc = Record('NPC_', FormID(form_id), 0)
    flags = 1 if female else 0
    npc.add_subrecord('ACBS', struct.pack('<I', flags) + b'\x00' * 20)
    schema = GameRegistry.get_game('tes5').get('NPC_')
    npc.bind_schema(schema)
    return npc


def _make_race(form_id=0x200, child=False):
    race = Record('RACE', FormID(form_id), 0)
    # DATA with flags at offset 32 (bit 2 = child)
    data = b'\x00' * 32 + struct.pack('<I', 4 if child else 0) + b'\x00' * 100
    race.add_subrecord('DATA', data)
    return race


class TestDetermineNPCSex:


    def test_male_adult(self):
        npc = _make_npc(female=False)
        race = _make_race(child=False)
        assert determine_npc_sex(npc, race) == Sex.MALE_ADULT


    def test_female_adult(self):
        npc = _make_npc(female=True)
        race = _make_race(child=False)
        assert determine_npc_sex(npc, race) == Sex.FEMALE_ADULT


    def test_male_child(self):
        npc = _make_npc(female=False)
        race = _make_race(child=True)
        assert determine_npc_sex(npc, race) == Sex.MALE_CHILD


    def test_female_child(self):
        npc = _make_npc(female=True)
        race = _make_race(child=True)
        assert determine_npc_sex(npc, race) == Sex.FEMALE_CHILD
