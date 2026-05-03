"""Integration test: Phase 1 of breeds — npc_races maps to a breed.

Uses `ungulate_test` scheme with `[npc_races] UraggroShub = "CapeBuffalo"`.
CapeBuffalo is registered as a BDMinoRace breed in
`races/yas_minorace.toml`. Verifies `determine_npc_race` returns the
breed in the 4-tuple so downstream phases can apply breed-specific
constraints. See PLAN_FURRIFIER_BREEDS.md.
"""
from __future__ import annotations

import pytest

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.context import FurryContext
from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla
from furrifier.furry_load import (
    load_races, load_headparts, build_race_headparts, build_race_tints)


MINO_PLUGINS = [
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
    "BDCatRaces.esp",
    "YASCanineRaces.esp",
    "BDUngulates.esp",
]


from conftest import plugins_available

requires_mino_files = pytest.mark.skipif(
    not plugins_available(MINO_PLUGINS),
    reason=f"required plugins missing: {MINO_PLUGINS}",
)

pytestmark = requires_mino_files


@pytest.fixture(scope="module")
def data_dir():
    d = find_game_data('tes5')
    if d is None:
        pytest.skip("Skyrim data files not found")
    return d


@pytest.fixture(scope="module")
def mino_plugin_set(data_dir):
    lo = LoadOrder.from_list(MINO_PLUGINS, data_dir=data_dir, game_id='tes5')
    ps = PluginSet(lo)
    strings_dir = find_strings_dir()
    if strings_dir:
        ps.string_search_dirs = [str(strings_dir)]
    ps.load_all()
    return ps


@pytest.fixture(scope="module")
def breed_furry(mino_plugin_set, data_dir):
    ctx = load_scheme('ungulate_test')
    setup_vanilla(ctx)
    races_by_edid_info = load_races(mino_plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid_info.items()}
    headparts = load_headparts(mino_plugin_set, ctx)
    race_headparts = build_race_headparts(list(mino_plugin_set), headparts)
    race_tints = build_race_tints(list(mino_plugin_set))
    patch = Plugin.new_plugin(data_dir / 'BreedPhase1TEST.esp')
    patch.plugin_set = mino_plugin_set
    return FurryContext(
        patch=patch, ctx=ctx, races=races,
        all_headparts=headparts, race_headparts=race_headparts,
        race_tints=race_tints, plugin_set=mino_plugin_set)


def test_capebuffalo_breed_registered(breed_furry):
    """Sanity check: CapeBuffalo is in the registry from races/*.toml."""
    assert 'CapeBuffalo' in breed_furry.ctx.breeds
    assert breed_furry.ctx.breeds['CapeBuffalo'].parent_race_edid == 'BDMinoRace'
    # Default probability per decision #13.
    assert breed_furry.ctx.breeds['CapeBuffalo'].probability == 0.0


def test_uraggro_shub_assigned_capebuffalo(breed_furry, mino_plugin_set):
    """ungulate_test.toml sets UraggroShub = "CapeBuffalo" in [npc_races];
    determine_npc_race should resolve to the breed and surface it as
    the 4th element of the return tuple. The engine race (3rd element)
    is the breed's parent BDMinoRace so RNAM rewriting and headpart-
    pool lookups work normally."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'UraggroShub')
    assert npc is not None, "UraggroShub not found in plugin set"
    result = breed_furry.determine_npc_race(npc)
    assert result is not None
    original, assigned, furry, breed = result
    assert original == 'OrcRace'
    assert assigned == 'CapeBuffalo'  # breed name surfaces here
    assert furry == 'BDMinoRace'      # engine race
    assert breed is not None
    assert breed.name == 'CapeBuffalo'
    assert breed.parent_race_edid == 'BDMinoRace'


def test_unbred_orc_returns_none_breed(breed_furry, mino_plugin_set):
    """An Orc not named in [npc_races] takes the normal vanilla→furry
    path (OrcRace → BDMinoRace) and gets no breed (CapeBuffalo's
    probability is 0, so the auto-roll never fires)."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'Borkul')
    assert npc is not None
    result = breed_furry.determine_npc_race(npc)
    assert result is not None
    original, assigned, furry, breed = result
    assert original == 'OrcRace'
    assert assigned == 'OrcRace'
    assert furry == 'BDMinoRace'
    assert breed is None
