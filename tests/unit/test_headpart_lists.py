"""Tests for headpart FormList furrification (F1)."""

import struct
import pytest
from pathlib import Path

from esplib import Plugin, Record, FormID
from esplib.record import SubRecord

from furrifier.context import FurryContext
from furrifier.race_defs import RaceDefContext
from furrifier.models import HeadpartInfo, HeadpartType, Sex


def _make_record(sig, form_id, edid=None):
    rec = Record(sig, FormID(form_id), 0)
    if edid:
        rec.add_subrecord('EDID', (edid + '\x00').encode('cp1252'))
    return rec


def _make_flst(form_id, edid, race_fids):
    """Create a FLST record containing race FormIDs as LNAM entries."""
    rec = _make_record('FLST', form_id, edid)
    for fid in race_fids:
        rec.add_subrecord('LNAM', struct.pack('<I', fid))
    return rec


def _make_hdpt(form_id, edid, flst_fid, hp_type=HeadpartType.HAIR,
               male=True, female=True):
    """Create an HDPT record with RNAM pointing to a FLST."""
    rec = _make_record('HDPT', form_id, edid)
    rec.add_subrecord('PNAM', struct.pack('<I', hp_type.value))
    rec.add_subrecord('RNAM', struct.pack('<I', flst_fid))
    flags = 0
    if male:
        flags |= 0x02
    if female:
        flags |= 0x04
    rec.add_subrecord('DATA', struct.pack('<B', flags))
    return rec


def _make_race(form_id, edid):
    rec = _make_record('RACE', form_id, edid)
    rec.add_subrecord('DATA', b'\x00' * 136)
    return rec


def _make_plugin(name, records):
    """Create a minimal plugin containing the given records."""
    plugin = Plugin.new_plugin(name, masters=['Skyrim.esm'])
    for rec in records:
        rec.plugin = plugin
        plugin.add_record(rec)
    return plugin


class TestFurrifyAllHeadpartLists:


    def _build_context(self, plugins, ctx, races_dict):
        """Build a FurryContext with minimal state."""
        patch = Plugin.new_plugin(
            'TestPatch.esp',
            masters=['Skyrim.esm', 'FurryMod.esm'],
        )
        return FurryContext(
            patch=patch,
            ctx=ctx,
            races=races_dict,
            all_headparts={},
            race_headparts={},
            race_tints={},
            plugin_set=None,
            max_tint_layers=200,
        )


    def test_removes_furrified_vanilla_race(self):
        """A FLST containing NordRace should have NordRace removed after
        furrification (NordRace -> LykaiosRace)."""
        nord = _make_race(0x00013746, 'NordRace')
        lykaios = _make_race(0x01000800, 'YASLykaiosRace')
        argonian = _make_race(0x00013740, 'ArgonianRace')

        flst = _make_flst(0x00050000, 'HeadPartsHairNordFlst',
                          [0x00013746, 0x00013740])  # Nord + Argonian
        hdpt = _make_hdpt(0x00060000, 'HairMaleNord01', 0x00050000)

        plugin = _make_plugin('Skyrim.esm', [nord, argonian, flst, hdpt])

        furry_plugin = _make_plugin('FurryMod.esm', [lykaios])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace')

        races = {
            'NordRace': nord,
            'YASLykaiosRace': lykaios,
            'ArgonianRace': argonian,
        }

        furry = self._build_context([plugin, furry_plugin], ctx, races)
        count = furry.furrify_all_headpart_lists([plugin, furry_plugin])

        assert count == 1

        # Check the patched FLST -- should have Argonian but not Nord
        patched_flsts = [r for r in furry.patch.get_records_by_signature('FLST')]
        assert len(patched_flsts) == 1
        lnams = patched_flsts[0].get_subrecords('LNAM')
        lnam_objs = [sr.get_uint32() & 0x00FFFFFF for sr in lnams]
        assert 0x13746 not in lnam_objs, "NordRace should be removed"
        assert 0x13740 in lnam_objs, "ArgonianRace should remain"


    def test_adds_furrified_races_for_furry_race(self):
        """If a FLST contains a furry race (LykaiosRace), the furrified
        vanilla race (NordRace obj_id) should be added."""
        nord = _make_race(0x00013746, 'NordRace')
        lykaios = _make_race(0x01000800, 'YASLykaiosRace')

        # FLST only has LykaiosRace
        flst = _make_flst(0x01050000, 'HeadPartsHairLykaiosFlst',
                          [0x01000800])
        hdpt = _make_hdpt(0x01060000, 'YASHairMaleLykaios01', 0x01050000)

        plugin = _make_plugin('Skyrim.esm', [nord])
        furry_plugin = _make_plugin('FurryMod.esm', [lykaios, flst, hdpt])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace')

        races = {
            'NordRace': nord,
            'YASLykaiosRace': lykaios,
        }

        furry = self._build_context([plugin, furry_plugin], ctx, races)
        count = furry.furrify_all_headpart_lists([plugin, furry_plugin])

        assert count == 1

        patched_flsts = [r for r in furry.patch.get_records_by_signature('FLST')]
        assert len(patched_flsts) == 1
        lnams = patched_flsts[0].get_subrecords('LNAM')
        lnam_objs = [sr.get_uint32() & 0x00FFFFFF for sr in lnams]
        # Should have both Lykaios and the furrified NordRace
        assert 0x0800 in lnam_objs, "LykaiosRace should remain"
        assert 0x13746 in lnam_objs, "Furrified NordRace should be added"


    def test_no_change_for_unrelated_races(self):
        """A FLST with only unaffected races should not be modified."""
        argonian = _make_race(0x00013740, 'ArgonianRace')
        khajiit = _make_race(0x00013745, 'KhajiitRace')

        flst = _make_flst(0x00050000, 'HeadPartsArgonianFlst',
                          [0x00013740, 0x00013745])
        hdpt = _make_hdpt(0x00060000, 'HairArgonian01', 0x00050000)

        plugin = _make_plugin('Skyrim.esm', [argonian, khajiit, flst, hdpt])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace')

        nord = _make_race(0x00013746, 'NordRace')
        lykaios = _make_race(0x01000800, 'YASLykaiosRace')
        races = {
            'NordRace': nord,
            'YASLykaiosRace': lykaios,
        }

        furry = self._build_context([plugin], ctx, races)
        count = furry.furrify_all_headpart_lists([plugin])

        assert count == 0


    def test_flst_processed_only_once(self):
        """Two HDPT records sharing the same FLST should only cause one
        override, not two."""
        nord = _make_race(0x00013746, 'NordRace')
        lykaios = _make_race(0x01000800, 'YASLykaiosRace')

        flst = _make_flst(0x00050000, 'SharedFlst', [0x00013746])
        hdpt1 = _make_hdpt(0x00060001, 'Hair01', 0x00050000)
        hdpt2 = _make_hdpt(0x00060002, 'Hair02', 0x00050000)

        plugin = _make_plugin('Skyrim.esm', [nord, flst, hdpt1, hdpt2])
        furry_plugin = _make_plugin('FurryMod.esm', [lykaios])

        ctx = RaceDefContext()
        ctx.set_race('NordRace', 'YASLykaiosRace')

        races = {'NordRace': nord, 'YASLykaiosRace': lykaios}
        furry = self._build_context([plugin, furry_plugin], ctx, races)
        count = furry.furrify_all_headpart_lists([plugin, furry_plugin])

        assert count == 1
