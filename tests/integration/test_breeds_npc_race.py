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
    # yas_minorace.toml gives CapeBuffalo a non-zero auto-roll weight.
    # Whatever the exact value, it must be > 0 for the auto-roll path
    # to fire on un-overridden Mino NPCs.
    assert breed_furry.ctx.breeds['CapeBuffalo'].probability > 0.0


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


def test_unforced_orc_takes_breed_from_auto_roll(breed_furry, mino_plugin_set):
    """An Orc not named in [npc_races] takes the normal vanilla→furry
    path (OrcRace → BDMinoRace). yas_minorace.toml's breed
    probabilities sum to 1.0 (every Mino is some breed), so the auto-
    roll always lands on a registered breed."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'Borkul')
    assert npc is not None
    result = breed_furry.determine_npc_race(npc)
    assert result is not None
    original, assigned, furry, breed = result
    assert original == 'OrcRace'
    assert assigned == 'OrcRace'
    assert furry == 'BDMinoRace'
    assert breed is not None, (
        "with breed probabilities summing to 1.0, every Mino NPC must "
        "land on a registered breed via the auto-roll")
    assert breed.parent_race_edid == 'BDMinoRace'


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


# ---------------------------------------------------------------------------
# Phase 3 — tint filtering by breed
# ---------------------------------------------------------------------------


import struct


def _patched_tints(patched):
    """Walk patched record's TINI/TINC/TINV subrecords in order, returning
    tuples of (tini_index, rgba)."""
    tints = []
    subs = patched.subrecords
    for i, sr in enumerate(subs):
        if sr.signature != 'TINI':
            continue
        tini = struct.unpack('<H', sr.data[:2])[0]
        rgba = None
        for j in range(i + 1, min(i + 4, len(subs))):
            if subs[j].signature == 'TINC' and subs[j].size >= 4:
                rgba = tuple(subs[j].data[:4])
                break
            if subs[j].signature == 'TINI':
                break
        tints.append((tini, rgba))
    return tints


def test_capebuffalo_emits_skintone_tint(breed_furry, mino_plugin_set):
    """CapeBuffalo's color scheme always emits SkinTone (TINI 1) at
    probability 1.0. Other masks may or may not fire depending on
    their probability rolls — this test only pins down SkinTone."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'UraggroShub')
    patched = breed_furry.furrify_npc(npc)
    tints = _patched_tints(patched)
    skintone_tints = [(tini, rgba) for tini, rgba in tints if tini == 1]
    assert len(skintone_tints) == 1, (
        f"CapeBuffalo SkinTone (TINI 1) must emit once; got {tints}")


def test_capebuffalo_skintone_color_from_whitelist(
        breed_furry, mino_plugin_set):
    """The SkinTone TINC color must be one of CapeBuffalo's whitelisted
    coat-color EDIDs (resolved through BDUngulates' current CNAMs)."""
    # Read CapeBuffalo's SkinTone whitelist live from the loaded scheme,
    # then resolve each EDID via the FurryContext's CLFM EDID index so
    # this test stays correct if Hugh tweaks the CNAMs in BDUngulates.
    scheme = breed_furry.ctx.color_schemes['CapeBuffalo']
    skintone_rule = next(r for r in scheme if r.mask_substring == 'SkinTone')
    allowed = set()
    for edid, _intensity in skintone_rule.color_choices:
        rgba = breed_furry._resolve_color_by_edid(edid)
        if rgba is not None:
            allowed.add(rgba)
    assert allowed, (
        "test premise broken — none of CapeBuffalo's SkinTone EDIDs "
        "resolved to a CLFM in the load order")

    npc = mino_plugin_set.get_record_by_edid('NPC_', 'UraggroShub')
    patched = breed_furry.furrify_npc(npc)
    tints = _patched_tints(patched)
    skintone = next(((tini, rgba) for tini, rgba in tints if tini == 1), None)
    assert skintone is not None, "no SkinTone tint emitted"
    _, rgba = skintone
    assert rgba in allowed, (
        f"TINC color {rgba} not in CapeBuffalo whitelist {allowed} — "
        f"either color resolution is wrong or the parent-preset filter "
        f"silently picked something else")


# ---------------------------------------------------------------------------
# Phase 5 — leveled-list breeds + override_furry_race breed support
# ---------------------------------------------------------------------------


def test_override_furry_race_resolves_breed_to_parent(
        breed_furry, mino_plugin_set):
    """furrify_npc(override_furry_race='CapeBuffalo') must resolve the
    breed name to its parent race (BDMinoRace) for RNAM, and still
    apply CapeBuffalo's headpart whitelist. This is the path taken by
    the leveled-list extender when its rule names a breed."""
    npc = mino_plugin_set.get_record_by_edid('NPC_', 'Borkul')
    assert npc is not None
    patched = breed_furry.furrify_npc(
        npc, override_furry_race='CapeBuffalo')
    assert patched is not None
    eyebrows = _pnam_edids_of_type(
        patched, breed_furry.all_headparts, HeadpartType.EYEBROWS)
    assert eyebrows == ['BDMinoCapeHorns'], (
        f"override-to-breed should pick the breed-whitelisted horn; "
        f"got {eyebrows}")


def test_breed_hair_whitelist_overrides_exclude_label(
        breed_furry, mino_plugin_set):
    """A breed whitelist explicitly names headparts by EDID. Those
    headparts must apply even when the catalog has tagged them with
    EXCLUDE — the EXCLUDE label suppresses random-pool selection, but
    a deliberate whitelist is explicit author intent and overrides.

    Bison's HAIR whitelist in yas_minorace.toml is the BDMino mane
    set, all of which carry headpart_labels = "EXCLUDE". Pre-fix this
    produced Bison NPCs with no HAIR PNAM (the whitelist intersected
    the EXCLUDE-filtered race pool to empty). Regression gate.
    """
    npc = mino_plugin_set.get_record_by_edid(
        'NPC_', 'EncBandit02Boss2HOrcM')
    assert npc is not None
    patched = breed_furry.furrify_npc(
        npc, override_furry_race='Bison')
    assert patched is not None
    hair = _pnam_edids_of_type(
        patched, breed_furry.all_headparts, HeadpartType.HAIR)
    assert hair, (
        "Bison override should produce a HAIR PNAM from the breed's "
        "mane whitelist; got none — likely the whitelist intersected "
        "the race pool (which excludes EXCLUDE-tagged hairs) to empty"
    )
    bison_whitelist = {
        'BDMinoHairFemMane', 'BDMinoHairFemManeCurly',
        'BDMinoHairFemManeFeather', 'BDMinoHairFemManeHeadband',
        'BDMinoHairFemManeRough', 'BDMinoHairMaleMane',
        'BDMinoHairMaleManeCurly', 'BDMinoHairMaleManeFeather',
        'BDMinoHairMaleManeHeadband', 'BDMinoHairMaleShaggy',
        'BDMinoHairMinoManeRough',
    }
    assert hair[0] in bison_whitelist, (
        f"HAIR pick {hair[0]!r} not in Bison's mane whitelist — "
        f"the whitelist filter isn't actually firing")


def test_extend_leveled_npcs_creates_breed_duplicate(
        breed_furry, mino_plugin_set):
    """The ungulate_test scheme has a `bandit` leveled-NPCs group with
    CapeBuffalo at probability=0.5. Running the extender against the
    real load order must produce at least one CapeBuffalo duplicate
    (RNAM = BDMinoRace, breed-whitelisted horn)."""
    breed_furry.furrify_all_npcs(list(mino_plugin_set))
    new_count, list_count = breed_furry.extend_leveled_npcs(
        list(mino_plugin_set))
    assert new_count > 0, "leveled-list extender produced no duplicates"
    assert list_count > 0
    # Find a CapeBuffalo duplicate by EditorID convention
    # (`YAS_<src>_<short_race>`); short_race_name strips the 'Race'
    # suffix but breed names don't have one, so it surfaces verbatim.
    dups = [r for r in breed_furry.patch.get_records_by_signature('NPC_')
            if (r.editor_id or '').endswith('_CapeBuffalo')]
    assert dups, (
        "no CapeBuffalo leveled duplicates created — breed-as-race "
        "wiring missing somewhere in extend_leveled_npcs")
    # And the duplicate must carry the breed's headpart constraint.
    eyebrows = _pnam_edids_of_type(
        dups[0], breed_furry.all_headparts, HeadpartType.EYEBROWS)
    assert eyebrows == ['BDMinoCapeHorns'], (
        f"CapeBuffalo duplicate {dups[0].editor_id!r} has eyebrows "
        f"{eyebrows}; expected the breed-whitelisted BDMinoCapeHorns")
