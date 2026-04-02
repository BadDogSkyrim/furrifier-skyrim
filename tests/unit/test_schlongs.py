"""Tests for SOS schlong furrification (F5b)."""

import struct
import pytest

from esplib import Plugin, Record, FormID
from esplib.vmad import VmadData, VmadScript, VmadProperty, VmadObject, PROP_OBJECT

from furrifier.schlongs import furrify_all_schlongs


def _make_record(sig, form_id, edid=None):
    rec = Record(sig, FormID(form_id), 0)
    if edid:
        rec.add_subrecord('EDID', (edid + '\x00').encode('cp1252'))
    return rec


def _make_flst(form_id, edid, fids):
    rec = _make_record('FLST', form_id, edid)
    for fid in fids:
        rec.add_subrecord('LNAM', struct.pack('<I', fid))
    return rec


def _make_glob(form_id, edid, value=1.0):
    rec = _make_record('GLOB', form_id, edid)
    rec.add_subrecord('FNAM', struct.pack('<B', 0))  # type = float
    rec.add_subrecord('FLTV', struct.pack('<f', value))
    return rec


def _make_sos_quest(form_id, edid, compat_fid, prob_fid, size_fid):
    """Create a QUST record with SOS_AddonQuest_Script VMAD."""
    rec = _make_record('QUST', form_id, edid)
    vmad = VmadData(version=5, obj_format=2)
    script = VmadScript(name='SOS_AddonQuest_Script', flags=0)
    script.properties.append(VmadProperty(
        name='SOS_Addon_CompatibleRaces', type=PROP_OBJECT, flags=1,
        value=VmadObject(form_id=compat_fid, alias=-1)))
    script.properties.append(VmadProperty(
        name='SOS_Addon_RaceProbabilities', type=PROP_OBJECT, flags=1,
        value=VmadObject(form_id=prob_fid, alias=-1)))
    script.properties.append(VmadProperty(
        name='SOS_Addon_RaceSizes', type=PROP_OBJECT, flags=1,
        value=VmadObject(form_id=size_fid, alias=-1)))
    vmad.scripts.append(script)
    rec.add_subrecord('VMAD', vmad.to_bytes())
    return rec


def _make_plugin(name, records):
    plugin = Plugin.new_plugin(name, masters=['Skyrim.esm'])
    for rec in records:
        rec.plugin = plugin
        plugin.add_record(rec)
    return plugin


class TestFurrifyAllSchlongs:


    def test_adds_furrified_race_to_sos_lists(self):
        """If a furry race is in the compat list, its furrified vanilla
        race should be added."""
        lykaios = _make_record('RACE', 0x01000800, 'YASLykaiosRace')
        nord = _make_record('RACE', 0x00013746, 'NordRace')

        # SOS FormLists with only LykaiosRace
        compat = _make_flst(0x02000100, 'SOSCompat', [0x01000800])
        prob_glob = _make_glob(0x02000200, 'SOSProbLykaios', 1.0)
        size_glob = _make_glob(0x02000300, 'SOSSizeLykaios', 1.0)
        prob = _make_flst(0x02000400, 'SOSProb', [0x02000200])
        size = _make_flst(0x02000500, 'SOSSize', [0x02000300])

        quest = _make_sos_quest(0x02000600, 'SOSTestQuest',
                                0x02000100, 0x02000400, 0x02000500)

        plugin = _make_plugin('Skyrim.esm', [nord])
        furry_plugin = _make_plugin('FurryMod.esm', [lykaios])
        sos_plugin = _make_plugin('SOS.esp',
                                  [compat, prob_glob, size_glob, prob, size, quest])

        patch = Plugin.new_plugin('TestPatch.esp',
                                  masters=['Skyrim.esm', 'FurryMod.esm', 'SOS.esp'])

        races = {
            'NordRace': nord,
            'YASLykaiosRace': lykaios,
        }
        race_assignments = {'NordRace': 'YASLykaiosRace'}
        furry_to_vanilla = {'YASLykaiosRace': ['NordRace']}

        count = furrify_all_schlongs(
            [plugin, furry_plugin, sos_plugin],
            patch, race_assignments, furry_to_vanilla, races,
        )

        assert count == 1

        # Check compat list was modified
        patched_flsts = list(patch.get_records_by_signature('FLST'))
        assert len(patched_flsts) >= 1


    def test_no_sos_quests_means_no_work(self):
        """If there are no SOS quests, nothing happens."""
        nord = _make_record('RACE', 0x00013746, 'NordRace')
        plugin = _make_plugin('Skyrim.esm', [nord])
        patch = Plugin.new_plugin('TestPatch.esp', masters=['Skyrim.esm'])

        count = furrify_all_schlongs(
            [plugin], patch, {}, {}, {'NordRace': nord},
        )
        assert count == 0
