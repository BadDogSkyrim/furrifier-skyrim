"""Tests for NPC furrification logic."""

import struct
import pytest

from esplib import Record, FormID
from esplib.defs.game import GameRegistry

from furrifier.npc import determine_npc_sex, determine_npc_race
from furrifier.models import Sex
from furrifier.race_defs import RaceDefContext

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas


def _make_npc(form_id=0x100, female=False, race_fid=0x200, edid='TestNPC'):
    npc = Record('NPC_', FormID(form_id), 0)
    npc.add_subrecord('EDID', (edid + '\x00').encode('cp1252'))
    flags = 1 if female else 0
    npc.add_subrecord('ACBS', struct.pack('<I', flags) + b'\x00' * 20)
    npc.add_subrecord('RNAM', struct.pack('<I', race_fid))
    schema = GameRegistry.get_game('tes5').get('NPC_')
    npc.bind_schema(schema)
    return npc


def _make_race(form_id=0x200, edid='NordRace', child=False):
    race = Record('RACE', FormID(form_id), 0)
    race.add_subrecord('EDID', (edid + '\x00').encode('cp1252'))
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


class TestDetermineNPCRace:
    def test_furrifiable_race(self):
        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')

        npc = _make_npc(race_fid=0x200)
        races = {'NordRace': _make_race(form_id=0x200, edid='NordRace')}

        result = determine_npc_race(npc, ctx, races)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'NordRace'
        assert assigned == 'NordRace'
        assert furry == 'YASLykaiosRace'

    def test_non_furrifiable_race(self):
        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')

        npc = _make_npc(race_fid=0x300)
        races = {'KhajiitRace': _make_race(form_id=0x300, edid='KhajiitRace')}

        result = determine_npc_race(npc, ctx, races)
        assert result is None

    def test_forced_npc_race(self):
        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')
        ctx.set_race('BretonRace', 'YASKygarraRace', 'DOG')
        ctx.set_subrace('YASReachmanRace', 'Reachman', 'BretonRace', 'YASKonoiRace', 'DOG')
        ctx.set_npc_race('Madanach', 'YASReachmanRace')

        npc = _make_npc(race_fid=0x400, edid='Madanach')
        races = {
            'BretonRace': _make_race(form_id=0x400, edid='BretonRace'),
        }

        result = determine_npc_race(npc, ctx, races)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'BretonRace'
        assert assigned == 'YASReachmanRace'
        assert furry == 'YASKonoiRace'

    def test_no_rnam(self):
        ctx = RaceDefContext()
        npc = Record('NPC_', FormID(0x100), 0)
        result = determine_npc_race(npc, ctx, {})
        assert result is None
