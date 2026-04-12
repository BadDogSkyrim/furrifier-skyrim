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
        ctx.set_race('NordRace', 'YASLykaiosRace')

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


    def test_preset_rnam_uses_normalized_formid(self, tmp_path):
        """Preset NPC's RNAM should correctly resolve to the furrified
        vanilla race even when the race is from a DLC (not the first master).

        Regression: RNAM was written using the patch-local FormID of the
        furrified race record. write_form_id expects load-order-normalized
        FormIDs, so the master index was wrong.
        """
        from esplib import PluginSet, LoadOrder

        # Three plugins: Skyrim -> Dawnguard (DLC1NordRace) -> FurryMod
        base = Plugin.new_plugin(tmp_path / 'Skyrim.esm')
        base.add_record(_make_race(0x00013746, 'NordRace'))
        base.save()

        dawn = Plugin.new_plugin(tmp_path / 'Dawnguard.esm',
                                 masters=['Skyrim.esm'])
        dawn.add_record(_make_race(0x0100E88A, 'DLC1NordRace'))
        dawn.save()

        furry_mod = Plugin.new_plugin(tmp_path / 'FurryMod.esm',
                                      masters=['Skyrim.esm', 'Dawnguard.esm'])
        furry_mod.add_record(
            _make_race(0x02000800, 'YASLykaiosRace',
                       preset_fids_male=[0x02000900]))
        furry_mod.add_record(
            _make_npc(0x02000900, 'YASLykaiosPreset01',
                      race_fid=0x02000800))
        furry_mod.save()

        lo = LoadOrder.from_list(
            ['Skyrim.esm', 'Dawnguard.esm', 'FurryMod.esm'],
            data_dir=tmp_path)
        ps = PluginSet(lo)
        ps.load_all()

        dawn_loaded = ps.get_plugin('Dawnguard.esm')
        furry_loaded = ps.get_plugin('FurryMod.esm')
        dlc1nord = [r for r in dawn_loaded.get_records_by_signature('RACE')
                    if r.editor_id == 'DLC1NordRace'][0]
        lykaios = [r for r in furry_loaded.get_records_by_signature('RACE')
                   if r.editor_id == 'YASLykaiosRace'][0]

        ctx = RaceDefContext()
        ctx.set_race('DLC1NordRace', 'YASLykaiosRace')

        patch = Plugin.new_plugin(tmp_path / 'TestPatch.esp')
        patch.plugin_set = ps

        furry = FurryContext(
            patch=patch, ctx=ctx,
            races={'DLC1NordRace': dlc1nord, 'YASLykaiosRace': lykaios},
            all_headparts={}, race_headparts={}, race_tints={},
            plugin_set=ps, max_tint_layers=200,
        )

        furry.furrify_race(dlc1nord, lykaios)
        count = furry.furrify_race_presets(list(ps))
        assert count == 1

        # Save and reload
        patch.save()
        reloaded = Plugin(tmp_path / 'TestPatch.esp')

        preset = None
        for rec in reloaded.records:
            if rec.signature == 'NPC_' and 'DLC1NordRace' in (rec.editor_id or ''):
                preset = rec
                break
        assert preset is not None, "Preset NPC not found in patch"

        # RNAM must point to Dawnguard.esm, not Skyrim.esm
        rnam_fid = struct.unpack('<I', preset.get_subrecord('RNAM').data)[0]
        file_idx = rnam_fid >> 24
        master_name = reloaded.header.masters[file_idx]
        assert master_name.lower() == 'dawnguard.esm', \
            f"RNAM master should be Dawnguard.esm, got {master_name}"
        assert (rnam_fid & 0x00FFFFFF) == 0x00E88A


    def test_no_presets_means_no_work(self):
        """A race with no presets should produce no new records."""
        lykaios = _make_race(0x01000800, 'YASLykaiosRace')
        nord = _make_race(0x00013746, 'NordRace')

        plugin = _make_plugin('Skyrim.esm', [nord])
        furry_plugin = _make_plugin('FurryMod.esm', [lykaios])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace')

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
