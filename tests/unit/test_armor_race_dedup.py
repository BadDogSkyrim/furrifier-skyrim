"""Adding a race to an ARMA dedups by EditorID, not FormID.

The races the armor pass adds include patch-CREATED subraces. Hugh's
working load order normally has a previous YASNPCPatch*.esp in front of
the new patch, carrying its own copy of every created subrace: same
EditorID, different patch-local FormID. A raw-FormID "already present?"
test misses that and adds the race a second time.

Measured on a real run before the fix: YASReachmanRace, YASReachmanRace-
Vampire, YASSkaalRace, YASSkaalRaceChild and YASSkaalRaceVampire were all
listed twice on YASDaedricHelmetAA_DOG, and YASWinterholdRace,
YASDragonBridgeRace and YASMoragTongRace twice on YAS_DaedricHelmetAA_CAT
-- e.g. 5C000800 (prior patch) alongside 5F000800 (new patch).

Same fix briarheart.py already carries. See [[patch-dedup-by-edid]].
"""
from __future__ import annotations

import struct
from types import SimpleNamespace

import pytest

from esplib import Plugin, PluginSet, Record
from esplib.load_order import LoadOrder
from esplib.utils import FormID

from furrifier.context import FurryContext
from furrifier.util import record_key

HEAD = 0x00000001


def _plugin(name, masters=None, is_esm=False):
    return Plugin.new_plugin(name, masters=masters, game='tes5',
                             is_esm=is_esm)


def _add(plugin, sig, form_id, edid, subrecords=()):
    rec = Record(sig, FormID(form_id), 0)
    rec.add_subrecord('EDID', edid.encode() + b'\x00')
    for s, data in subrecords:
        rec.add_subrecord(s, data)
    plugin.add_record(rec)
    return rec


def _plugin_set(*plugins):
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
def world():
    """Two furry races on one ARMA. One maps to a subrace the ARMA
    ALREADY carries via the prior patch's copy (must not be re-added);
    the other maps to a subrace it doesn't have (must be added).

    Both halves matter: without the second, a correct dedup leaves
    nothing to write, the patch gets no ARMA override at all, and the
    assertions below would have nothing to inspect.
    """
    races = _plugin('Races.esp')
    furry = _add(races, 'RACE', 0x00000800, 'FurryRace')
    furry2 = _add(races, 'RACE', 0x00000801, 'FurryRace2')

    # Stands in for a previous furrifier run still in the load order.
    prior = _plugin('PriorPatch.esp', masters=['Races.esp'])
    prior_sub = _add(prior, 'RACE', 0x01000900, 'YASReachmanRace')

    # The ARMA already lists the prior patch's subrace.
    armor = _plugin('Armor.esp', masters=['Races.esp', 'PriorPatch.esp'])
    arma = _add(
        armor, 'ARMA', 0x02000800, 'HelmetAA_Furry',
        [('BOD2', struct.pack('<II', HEAD, 0)),
         ('RNAM', b'\x00\x08\x00\x00'),          # Races.esp FurryRace
         ('MODL', b'\x00\x09\x00\x01'),          # PriorPatch YASReachmanRace
         ('MODL', b'\x01\x08\x00\x00')])         # Races.esp FurryRace2
    _add(armor, 'ARMO', 0x02000801, 'Helmet',
         [('MODL', b'\x00\x08\x00\x02')])        # its own ARMA

    patch = _plugin('NewPatch.esp',
                    masters=['Races.esp', 'PriorPatch.esp', 'Armor.esp'])
    # The new run's own copy of the subrace: same EditorID, its own FormID.
    new_sub = _add(patch, 'RACE', 0x03000900, 'YASReachmanRace')
    other_sub = _add(patch, 'RACE', 0x03000901, 'YASSkaalRace')

    ps = _plugin_set(races, prior, armor, patch)

    ctx = SimpleNamespace(
        assignments={},
        subraces={
            'a': SimpleNamespace(furry_id='FurryRace',
                                 name='YASReachmanRace'),
            'b': SimpleNamespace(furry_id='FurryRace2',
                                 name='YASSkaalRace'),
        },
    )
    furry_ctx = FurryContext(
        patch=patch, ctx=ctx,
        races={'FurryRace': furry, 'FurryRace2': furry2,
               'YASReachmanRace': new_sub, 'YASSkaalRace': other_sub},
        all_headparts={}, race_headparts={}, race_tints={}, plugin_set=ps)

    furry_ctx.furrify_all_armor([races, prior, armor])
    return ps, patch, arma, prior_sub, new_sub


LOCAL = 0xFF000000


def _race_edid_index(plugins, patch):
    """Race key -> EditorID, registering patch records under BOTH their
    record key and the local sentinel -- a reference from inside the patch
    to its own record is written as 0xFF|objidx, so an index built only
    from record_key() would fail to resolve exactly the entries this test
    is about, and every assertion below would pass vacuously."""
    idx = {}
    for pl in plugins:
        for r in pl.get_records_by_signature('RACE'):
            idx[record_key(r)] = r.editor_id
    for r in patch.get_records_by_signature('RACE'):
        idx[record_key(r)] = r.editor_id
        idx[LOCAL | (r.form_id.value & 0x00FFFFFF)] = r.editor_id
    return idx


def _patched_arma(patch, arma):
    """The patch's override of `arma`. Absent means the armor pass did
    nothing, which would make these assertions meaningless -- so the
    callers require it."""
    for r in patch.get_records_by_signature('ARMA'):
        if record_key(r) == record_key(arma):
            return r
    return None


def _race_entries(rec):
    return [rec.normalize_form_id(sr.get_form_id()).value
            for sr in rec.get_subrecords('MODL') if sr.size >= 4]


class TestArmaRaceDedup:

    def test_fixture_has_two_records_for_one_editor_id(self, world):
        _ps, _patch, _arma, prior_sub, new_sub = world
        assert prior_sub.editor_id == new_sub.editor_id
        assert record_key(prior_sub) != record_key(new_sub), \
            "fixture must model same-EditorID-different-FormID"

    def test_the_armor_pass_actually_touched_the_arma(self, world):
        """Guard: without an override in the patch the other two tests
        would be inspecting the untouched source record and passing for
        no reason."""
        ps, patch, arma, _prior, _new = world
        patched = _patched_arma(patch, arma)
        assert patched is not None, \
            "furrify_all_armor produced no override; fixture is inert"
        edids = _race_edid_index(ps, patch)
        names = [edids.get(e, f'{e:08X}')
                 for e in _race_entries(patched)]
        assert 'YASSkaalRace' in names, \
            f"the legitimate add did not happen: {names}"

    def test_subrace_is_not_added_twice(self, world):
        """The bug: the prior patch's copy is already on the ARMA, so the
        new patch's copy must not be appended alongside it."""
        ps, patch, arma, _prior, _new = world
        edids = _race_edid_index(ps, patch)
        entries = _race_entries(_patched_arma(patch, arma))
        names = [edids.get(e, f'{e:08X}') for e in entries]
        assert names.count('YASReachmanRace') == 1, (
            f"YASReachmanRace listed {names.count('YASReachmanRace')} "
            f"times: {names} (raw {[f'{e:08X}' for e in entries]})")

    def test_no_duplicate_editor_ids_at_all(self, world):
        ps, patch, arma, _prior, _new = world
        edids = _race_edid_index(ps, patch)
        entries = _race_entries(_patched_arma(patch, arma))
        seen = [edids.get(e, f'{e:08X}') for e in entries]
        dupes = {n for n in seen if seen.count(n) > 1}
        assert not dupes, f"races listed twice on the ARMA: {sorted(dupes)}"
