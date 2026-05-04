"""Integration tests for breeds Phase 1 + 2.

Uses `ungulate_test` scheme with `[npc_races] UraggroShub = "CapeBuffalo"`.
CapeBuffalo is registered as a BDMinoRace breed in
`races/yas_minorace.toml`, with EYEBROWS whitelisted to BDMinoCapeHorns
and FACIAL_HAIR disabled.

Phase 1: `determine_npc_race` exposes the assigned breed.
Phase 2: the patched NPC's PNAM list reflects the breed's headpart
constraints — only whitelisted EYEBROWS, no FACIAL_HAIR, HAIR
unconstrained (inherits parent BDMinoRace pool).

See PLAN_FURRIFIER_BREEDS.md.
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


# ---------------------------------------------------------------------------
# Phase 2 — headpart filtering by breed
# ---------------------------------------------------------------------------


from furrifier.models import HeadpartType


def _pnam_edids_of_type(patched, all_headparts, hp_type: HeadpartType):
    """EditorIDs of patched PNAM entries matching the requested type."""
    edids = []
    for sr in patched.get_subrecords('PNAM'):
        obj_id = sr.get_uint32() & 0x00FFFFFF
        for hp_id, hp in all_headparts.items():
            if (hp.record and hp.hp_type == hp_type
                    and (hp.record.form_id.value & 0x00FFFFFF) == obj_id):
                edids.append(hp_id)
                break
    return edids


def test_capebuffalo_eyebrows_constrained_to_whitelist(
        breed_furry, mino_plugin_set):
    """CapeBuffalo's EYEBROWS rule whitelists ['BDMinoCapeHorns'] —
    UraggroShub must end up with that exact horn, not whatever the
    breed-less Mino pool would produce by default."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'UraggroShub')
    assert npc is not None
    patched = breed_furry.furrify_npc(npc)
    assert patched is not None
    eyebrows = _pnam_edids_of_type(
        patched, breed_furry.all_headparts, HeadpartType.EYEBROWS)
    assert eyebrows == ['BDMinoCapeHorns'], (
        f"UraggroShub-as-CapeBuffalo should get only the whitelisted "
        f"BDMinoCapeHorns; got {eyebrows}")


def test_capebuffalo_facial_hair_disabled(breed_furry, mino_plugin_set):
    """CapeBuffalo's FACIAL_HAIR=0.0 → never assigned. Phase 2 should
    suppress facial hair even though the parent BDMinoRace's male rule
    is FACIAL_HAIR=0.5 (decision #5 inheritance: breed's explicit 0.0
    overrides the parent)."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'UraggroShub')
    patched = breed_furry.furrify_npc(npc)
    facial = _pnam_edids_of_type(
        patched, breed_furry.all_headparts, HeadpartType.FACIAL_HAIR)
    assert facial == [], (
        f"CapeBuffalo should suppress FACIAL_HAIR; got {facial}")


def test_capebuffalo_hair_inherits_unconstrained_pool(
        breed_furry, mino_plugin_set):
    """CapeBuffalo doesn't define HAIR rules → inherits BDMinoRace's
    unconstrained pool. Whatever HAIR is picked, it must come from the
    full Mino male hair pool, not be filtered to a one-element list."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'UraggroShub')
    patched = breed_furry.furrify_npc(npc)
    hair = _pnam_edids_of_type(
        patched, breed_furry.all_headparts, HeadpartType.HAIR)
    bdmino_male_hair = breed_furry.race_headparts.get(
        (HeadpartType.HAIR, 0, 'BDMinoRace'), set())
    assert bdmino_male_hair, (
        "test premise broken — BDMinoRace male hair pool is empty")
    if hair:
        assert hair[0] in bdmino_male_hair, (
            f"CapeBuffalo HAIR pick {hair[0]!r} not in BDMinoRace's "
            f"male hair pool — looks like the breed accidentally "
            f"narrowed the unconstrained slot")
