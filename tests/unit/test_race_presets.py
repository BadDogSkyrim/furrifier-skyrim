"""Tests for race preset furrification (F2)."""

import struct
import pytest

from esplib import Plugin, Record, FormID
from esplib.record import SubRecord

from furrifier.context import FurryContext
from furrifier.race_defs import RaceDefContext


def _make_record(sig, form_id, edid=None):
    rec = Record(sig, FormID(form_id), 0)
    if edid:
        rec.add_subrecord('EDID', (edid + '\x00').encode('cp1252'))
    return rec


def _make_race(form_id, edid, preset_fids_male=None, preset_fids_female=None):
    """Create a RACE record with optional preset references."""
    rec = _make_record('RACE', form_id, edid)
    rec.add_subrecord('DATA', b'\x00' * 136)
    # Add WNAM and RNAM as dummy FormIDs (needed for furrify_race)
    rec.add_subrecord('WNAM', struct.pack('<I', 0))
    rec.add_subrecord('RNAM', struct.pack('<I', 0))
    # Head data markers
    rec.add_subrecord('NAM0', b'')
    rec.add_subrecord('MNAM', b'')
    if preset_fids_male:
        for fid in preset_fids_male:
            rec.add_subrecord('RPRM', struct.pack('<I', fid))
    rec.add_subrecord('NAM0', b'')
    rec.add_subrecord('FNAM', b'')
    if preset_fids_female:
        for fid in preset_fids_female:
            rec.add_subrecord('RPRF', struct.pack('<I', fid))
    return rec


def _make_npc(form_id, edid, race_fid=0):
    rec = _make_record('NPC_', form_id, edid)
    rec.add_subrecord('RNAM', struct.pack('<I', race_fid))
    # Minimal ACBS
    rec.add_subrecord('ACBS', b'\x00' * 24)
    return rec


def _make_plugin(name, records):
    plugin = Plugin.new_plugin(name, masters=['Skyrim.esm'])
    for rec in records:
        rec.plugin = plugin
        plugin.add_record(rec)
    return plugin


class TestFurrifyRacePresets:


    def test_creates_new_preset_npcs(self):
        """Furrifying presets should create new NPC records with correct
        EDID and RNAM."""
        lykaios = _make_race(0x01000800, 'YASLykaiosRace',
                             preset_fids_male=[0x01000900])
        nord = _make_race(0x00013746, 'NordRace')
        preset_npc = _make_npc(0x01000900, 'YASLykaiosPreset01',
                               race_fid=0x01000800)

        plugin = _make_plugin('Skyrim.esm', [nord])
        furry_plugin = _make_plugin('FurryMod.esm',
                                    [lykaios, preset_npc])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')

        races = {
            'NordRace': nord,
            'YASLykaiosRace': lykaios,
        }

        patch = Plugin.new_plugin(
            'TestPatch.esp',
            masters=['Skyrim.esm', 'FurryMod.esm'],
        )
        furry = FurryContext(
            patch=patch, ctx=ctx, races=races,
            all_headparts={}, race_headparts={}, race_tints={},
            plugin_set=None, max_tint_layers=200,
        )

        # First furrify the race (creates the override in the patch)
        furry.furrify_race(nord, lykaios)

        # Now furrify presets
        count = furry.furrify_race_presets([plugin, furry_plugin])
        assert count == 1

        # Check that a new NPC was created
        npcs = list(patch.get_records_by_signature('NPC_'))
        assert len(npcs) >= 1

        new_preset = npcs[-1]
        assert 'NordRace' in new_preset.editor_id
        assert 'YASLykaiosPreset01' in new_preset.editor_id


    def test_no_presets_means_no_work(self):
        """A race with no presets should produce no new records."""
        lykaios = _make_race(0x01000800, 'YASLykaiosRace')
        nord = _make_race(0x00013746, 'NordRace')

        plugin = _make_plugin('Skyrim.esm', [nord])
        furry_plugin = _make_plugin('FurryMod.esm', [lykaios])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace', 'DOG')

        races = {'NordRace': nord, 'YASLykaiosRace': lykaios}
        patch = Plugin.new_plugin(
            'TestPatch.esp',
            masters=['Skyrim.esm', 'FurryMod.esm'],
        )
        furry = FurryContext(
            patch=patch, ctx=ctx, races=races,
            all_headparts={}, race_headparts={}, race_tints={},
        )

        furry.furrify_race(nord, lykaios)
        count = furry.furrify_race_presets([plugin, furry_plugin])
        assert count == 0
