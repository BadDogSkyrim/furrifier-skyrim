"""Integration tests for briarheart-specific armor wiring.

YASFurryWorld replaces the briarheart body mesh with a chest-hole version
in three ARMAs (YASBriarHeartAA / EmptyAA / BodyAA). For furrified
briarhearts (Forsworn -> YASReachmanRace) to equip them, the assigned
furry race has to be in each ARMA's Additional Races list. Also, the
USKPArmorBriarHeartBody ARMO's Armature must point at YASBriarHeartBodyAA
only -- merge_armor_overrides re-introduces USSEP's body ARMA, which the
briarheart patch then drops.
"""

import pytest

import esplib.defs.tes5  # noqa: F401
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.briarheart import patch_briarheart_armor
from furrifier.context import FurryContext
from furrifier.furry_load import (
    build_race_headparts, build_race_tints, load_headparts, load_races,
)
from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla

from conftest import plugins_available


BRIARHEART_PLUGINS = [
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
    "Unofficial Skyrim Special Edition Patch.esp",
    "BDCatRaces.esp",
    "YASCanineRaces.esp",
    "YASFurryWorld.esp",
]


requires_briarheart_files = pytest.mark.skipif(
    not plugins_available(BRIARHEART_PLUGINS),
    reason=f"required plugins missing: {BRIARHEART_PLUGINS}",
)

pytestmark = requires_briarheart_files


@pytest.fixture(scope="module")
def data_dir():
    d = find_game_data('tes5')
    if d is None:
        pytest.skip("Skyrim data files not found")
    return d


@pytest.fixture(scope="module")
def plugin_set(data_dir):
    lo = LoadOrder.from_list(BRIARHEART_PLUGINS, data_dir=data_dir,
                             game_id='tes5')
    ps = PluginSet(lo)
    sd = find_strings_dir()
    if sd:
        ps.string_search_dirs = [str(sd)]
    ps.load_all()
    return ps


@pytest.fixture(scope="module")
def briarheart_result(plugin_set, data_dir):
    """Fully-furrified state ready for the briarheart patch.

    Runs the same setup as a real run: race furrification + the single
    sample NPC furrification. The patch is injected into plugin_set so
    plugin_set lookups resolve to the patched NPC (whose RNAM now points
    at YASReachmanRace).
    """
    from furrifier.facegen import _inject_patch_into_plugin_set

    ctx = load_scheme('all_races_test')
    setup_vanilla(ctx)

    patch = Plugin.new_plugin(data_dir / 'BriarheartTEST.esp')
    patch.plugin_set = plugin_set

    races_by_edid_info = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid_info.items()}
    headparts = load_headparts(plugin_set, ctx)
    race_headparts = build_race_headparts(list(plugin_set), headparts)
    race_tints = build_race_tints(list(plugin_set))

    furry = FurryContext(
        patch=patch, ctx=ctx, races=races,
        all_headparts=headparts, race_headparts=race_headparts,
        race_tints=race_tints, plugin_set=plugin_set)
    furry.furrify_all_races()

    # Furrify just the sample briarheart so its RNAM ends up pointing at
    # the patch-local YASReachmanRace subrace record.
    sample = plugin_set.get_record_by_edid(
        'NPC_', 'EncForsworn02BossMagicBretonM01')
    assert sample is not None, \
        "EncForsworn02BossMagicBretonM01 not in load order"
    furrified_sample = furry.furrify_npc(sample)
    assert furrified_sample is not None, \
        "Sample briarheart was skipped by furrify_npc"

    # Match the production pipeline: merge ARMO overrides first so the
    # USKPArmorBriarHeartBody patch entry exists with the merged MODL
    # list the briarheart patcher needs to fix up.
    furry.merge_armor_overrides(plugin_set)

    _inject_patch_into_plugin_set(plugin_set, patch)
    count = patch_briarheart_armor(plugin_set, patch)

    return patch, count, furry.races


def _patched_arma(patch, edid):
    for rec in patch.get_records_by_signature('ARMA'):
        if rec.editor_id == edid:
            return rec
    return None


def _patched_armo(patch, edid):
    for rec in patch.get_records_by_signature('ARMO'):
        if rec.editor_id == edid:
            return rec
    return None


def _modl_fids(rec):
    """Set of local FormID values from a record's MODL subrecords."""
    return {sr.get_uint32() for sr in rec.get_subrecords('MODL')
            if sr.size >= 4}


def _reachman_local_fid(patch, races):
    """The patch-local FormID for YASReachmanRace, encoded as the bytes
    a MODL subrecord would carry."""
    reachman = races['YASReachmanRace']
    return patch.denormalize_form_id(
        reachman.normalize_form_id(reachman.form_id))


def test_patch_runs_when_yasfurryworld_present(briarheart_result):
    _, count, _ = briarheart_result
    # 3 ARMAs + 3 ARMOs == 6 records touched.
    assert count == 6


@pytest.mark.parametrize("arma_edid", [
    'YASBriarHeartAA', 'YASBriarHeartEmptyAA', 'YASBriarHeartBodyAA',
])
def test_briarheart_arma_gains_reachman_race(briarheart_result, arma_edid):
    patch, _, races = briarheart_result
    arma = _patched_arma(patch, arma_edid)
    assert arma is not None, f"{arma_edid} should have a patch override"
    reachman_local = _reachman_local_fid(patch, races)
    assert reachman_local in _modl_fids(arma), \
        f"YASReachmanRace ({hex(reachman_local)}) missing from {arma_edid}"


@pytest.mark.parametrize("armo_edid,yas_arma_edid", [
    ('USKPArmorBriarHeartBody', 'YASBriarHeartBodyAA'),
    ('ArmorBriarHeart',         'YASBriarHeartAA'),
    ('ArmorBriarHeartEmpty',    'YASBriarHeartEmptyAA'),
])
def test_briarheart_armo_points_only_at_yas_arma(briarheart_result,
                                                 plugin_set,
                                                 armo_edid, yas_arma_edid):
    patch, _, _ = briarheart_result
    armo = _patched_armo(patch, armo_edid)
    assert armo is not None, \
        f"{armo_edid} should have a patch override"

    yas_arma = plugin_set.get_record_by_edid('ARMA', yas_arma_edid)
    yas_local = patch.denormalize_form_id(
        yas_arma.normalize_form_id(yas_arma.form_id))

    modls = _modl_fids(armo)
    assert modls == {yas_local}, \
        (f"{armo_edid} MODL should be exactly {{{yas_arma_edid}}}, "
         f"got {modls}")


def test_running_twice_does_not_duplicate_race(briarheart_result, plugin_set):
    """Re-running the patcher must not append a second MODL ref for a
    race that's already in the ARMA's list (the live-load-order bug
    where a prior patch already added YASReachmanRace was masked because
    its FormID lives under a different master index)."""
    from furrifier.briarheart import patch_briarheart_armor

    patch, _, _ = briarheart_result
    before = {
        edid: len(_modl_fids(_patched_arma(patch, edid)))
        for edid in ('YASBriarHeartAA', 'YASBriarHeartEmptyAA',
                     'YASBriarHeartBodyAA')
    }
    # Second invocation: ARMOs get rewritten (no-op net effect) but no
    # ARMA should gain another MODL entry.
    patch_briarheart_armor(plugin_set, patch)
    after = {
        edid: len(_modl_fids(_patched_arma(patch, edid)))
        for edid in before
    }
    assert before == after, \
        f"ARMA MODL counts changed on re-run: {before} -> {after}"


def test_patch_is_noop_without_yasfurryworld(data_dir):
    """Skip cleanly when YASFurryWorld.esp isn't loaded."""
    plugins = [p for p in BRIARHEART_PLUGINS if p != 'YASFurryWorld.esp']
    lo = LoadOrder.from_list(plugins, data_dir=data_dir, game_id='tes5')
    ps = PluginSet(lo)
    sd = find_strings_dir()
    if sd:
        ps.string_search_dirs = [str(sd)]
    ps.load_all()
    patch = Plugin.new_plugin(data_dir / 'BriarheartNoopTEST.esp')
    patch.plugin_set = ps
    assert patch_briarheart_armor(ps, patch) == 0
