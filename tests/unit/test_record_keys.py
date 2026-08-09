"""Record identity must be the load-order FormID, never `& 0xFFFFFF`.

Every plugin numbers its own records from 0x000800, so the low 24 bits
of a FormID collide constantly across plugins -- 240 NPC_, 159 ARMA and
127 ARMO records vanished from a normal load order while dicts were
keyed on them. Light (ESL/ESPFE) plugins are worse: the low 24 bits are
not the object index at all.

Everything here builds real in-memory plugins wired into a real
PluginSet, so `normalize_form_id` does the actual master-list ->
load-order mapping rather than a stub's identity function.

See PLAN_FURRIFIER_FORMID_KEYS.md.
"""
from __future__ import annotations

import pytest

from esplib import Plugin, PluginSet, Record
from esplib.load_order import LoadOrder
from esplib.utils import FormID

from furrifier.util import record_key, ref_key


def _plugin(name: str, masters=None, is_esl: bool = False) -> Plugin:
    p = Plugin.new_plugin(name, masters=masters, game='tes5')
    p.header.is_esl = is_esl
    return p


def _add(plugin: Plugin, sig: str, obj_id: int, edid: str) -> Record:
    """Add a record at `obj_id` with the plugin's OWN file index, which is
    how a plugin refers to a record it defines."""
    rec = Record(sig, FormID((len(plugin.header.masters) << 24) | obj_id), 0)
    rec.add_subrecord('EDID', edid.encode() + b'\x00')
    plugin.add_record(rec)
    return rec


def _plugin_set(*plugins: Plugin) -> PluginSet:
    lo = LoadOrder.from_list([p.file_path.name for p in plugins],
                             game_id='tes5')
    ps = PluginSet(lo)
    for p in plugins:
        ps._plugins[p.file_path.name] = p
        ps._loaded_full[p.file_path.name] = True
        p.plugin_set = ps
    ps.invalidate()
    return ps


@pytest.fixture
def colliding():
    """Two plugins that each define a record at object index 0x000800 --
    the real CellanRace / YASKaloRace situation, in miniature."""
    a = _plugin('AAA.esp')
    b = _plugin('BBB.esp')
    rec_a = _add(a, 'NPC_', 0x000800, 'AaaNpc')
    rec_b = _add(b, 'NPC_', 0x000800, 'BbbNpc')
    ps = _plugin_set(a, b)
    return ps, rec_a, rec_b


class TestRecordKey:

    def test_same_object_index_different_plugins_stay_distinct(
            self, colliding):
        _ps, rec_a, rec_b = colliding
        assert rec_a.form_id.value & 0xFFFFFF \
            == rec_b.form_id.value & 0xFFFFFF, \
            "fixture must actually collide on object index"
        assert record_key(rec_a) != record_key(rec_b)

    def test_dict_keyed_by_record_key_keeps_both(self, colliding):
        _ps, rec_a, rec_b = colliding
        winning = {}
        for rec in (rec_a, rec_b):
            winning[record_key(rec)] = rec
        assert len(winning) == 2

    def test_dict_keyed_by_object_index_loses_one(self, colliding):
        """The bug this file exists to prevent, pinned so the fixture
        stays honest."""
        _ps, rec_a, rec_b = colliding
        winning = {}
        for rec in (rec_a, rec_b):
            winning[rec.form_id.value & 0xFFFFFF] = rec
        assert len(winning) == 1

    def test_override_of_same_record_collapses(self):
        """Two plugins overriding ONE record must share a key -- that is
        the whole point of a winning-override map."""
        base = _plugin('Base.esm')
        rec_base = _add(base, 'NPC_', 0x000800, 'Shared')
        over = _plugin('Over.esp', masters=['Base.esm'])
        rec_over = Record('NPC_', FormID(0x00000800), 0)   # master index 0
        rec_over.add_subrecord('EDID', b'Shared\x00')
        over.add_record(rec_over)
        _plugin_set(base, over)
        assert record_key(rec_base) == record_key(rec_over)

    def test_light_plugin_record_is_in_the_fe_namespace(self):
        """An ESPFE record's key must carry 0xFE and its light ordinal --
        `& 0xFFFFFF` on the raw FormID would yield 0x000800 for every
        light plugin in the load order."""
        full = _plugin('Full.esp')
        light = _plugin('Light.esp', is_esl=True)
        rec_full = _add(full, 'NPC_', 0x000800, 'FullNpc')
        rec_light = _add(light, 'NPC_', 0x000800, 'LightNpc')
        _plugin_set(full, light)
        assert (record_key(rec_light) >> 24) == 0xFE
        assert record_key(rec_full) != record_key(rec_light)


class TestRefKey:

    def test_reference_resolves_to_the_referent_key(self):
        """A map built with `record_key` must be readable with `ref_key`
        -- producer and consumer sharing one keyspace is the invariant
        that broke the leveled-list lookup."""
        base = _plugin('Base.esm')
        target = _add(base, 'NPC_', 0x000800, 'Target')
        holder_plugin = _plugin('Holder.esp', masters=['Base.esm'])
        holder = _add(holder_plugin, 'LVLN', 0x000801, 'Holder')
        # master index 0 == Base.esm from Holder.esp's point of view
        holder.add_subrecord('LVLO', b'\x00' * 4 + b'\x00\x08\x00\x00'
                             + b'\x00' * 4)
        _plugin_set(base, holder_plugin)

        by_key = {record_key(target): target}
        sr = holder.get_subrecord('LVLO')
        assert by_key.get(ref_key(holder, sr.get_form_id(4))) is target

    def test_null_reference_is_none(self):
        p = _plugin('Solo.esp')
        rec = _add(p, 'NPC_', 0x000800, 'Solo')
        _plugin_set(p)
        assert ref_key(rec, FormID(0)) is None
        assert ref_key(rec, 0) is None
        assert ref_key(rec, None) is None

    def test_null_reference_does_not_match_a_real_record(self):
        """Returning 0 instead of None would match whatever record sits
        at key 0 -- the failure the guard exists for."""
        p = _plugin('Solo.esp')
        rec = _add(p, 'NPC_', 0x000800, 'Solo')
        _plugin_set(p)
        by_key = {0: 'something at key zero'}
        assert by_key.get(ref_key(rec, FormID(0))) is None
