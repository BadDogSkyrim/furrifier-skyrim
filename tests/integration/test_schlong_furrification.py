"""Integration tests for SOS schlong furrification.

Loads real SOS plugins (BDCatRaceSchlongs.esp, YASCanineSchlongs.esp),
runs furrify_all_schlongs, and verifies that vanilla races and subraces
appear in the compatible race lists.

Uses its own PluginSet and patch to avoid the stale-master-index problem
(esplib bug: adding masters after write_form_id shifts the self-index
but doesn't update previously-written FormID bytes).
"""

import pytest

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.context import FurryContext
from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla
from furrifier.furry_load import (
    load_races, load_headparts, build_race_headparts, build_race_tints)
from furrifier.schlongs import furrify_all_schlongs


def _find_data_dir():
    return find_game_data('tes5')


SCHLONG_PLUGINS = [
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
    "Schlongs of Skyrim - Core.esm",
    "BDCatRaces.esp",
    "YASCanineRaces.esp",
    "Schlongs of Skyrim.esp",
    "BadDogSchlongCore.esp",
    "BDCatRaceSchlongs.esp",
    "YASCanineSchlongs.esp",
]

requires_schlong_files = pytest.mark.skipif(
    _find_data_dir() is None,
    reason="Skyrim data files not found",
)

pytestmark = requires_schlong_files


# -- Fixtures --


@pytest.fixture(scope="module")
def data_dir():
    d = _find_data_dir()
    if d is None:
        pytest.skip("Skyrim data files not found")
    return d


@pytest.fixture(scope="module")
def ctx():
    c = load_scheme('all_races')
    setup_vanilla(c)
    return c


@pytest.fixture(scope="module")
def plugin_set(data_dir):
    lo = LoadOrder.from_list(SCHLONG_PLUGINS, data_dir=data_dir, game_id='tes5')
    ps = PluginSet(lo)
    strings_dir = find_strings_dir()
    if strings_dir:
        ps.string_search_dirs = [str(strings_dir)]
    ps.load_all()
    return ps


@pytest.fixture(scope="module")
def schlong_result(plugin_set, ctx, data_dir):
    """Run race + schlong furrification and return (patch, races_by_edid).

    Creates a fresh patch, furrifies all races (which creates subraces
    like YASWinterholdRace and YASReachmanRace), then builds the schlong
    maps and runs furrify_all_schlongs.
    """
    patch = Plugin.new_plugin(data_dir / 'SchlongTEST.esp')
    patch.plugin_set = plugin_set

    # Load race and headpart data
    races_by_edid_info = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid_info.items()}
    headparts = load_headparts(plugin_set, ctx)
    race_headparts = build_race_headparts(list(plugin_set), headparts)
    race_tints = build_race_tints(list(plugin_set))

    # Furrify races (creates subraces with fresh FormIDs in the patch)
    furry = FurryContext(
        patch=patch, ctx=ctx, races=races,
        all_headparts=headparts, race_headparts=race_headparts,
        race_tints=race_tints, plugin_set=plugin_set)
    furry.furrify_all_races()

    # Build schlong maps the same way main.py does
    race_assignments = {a.vanilla_id: a.furry_id
                        for a in ctx.assignments.values()}
    furry_to_vanilla: dict[str, list[str]] = {}
    for a in ctx.assignments.values():
        furry_to_vanilla.setdefault(a.furry_id, []).append(a.vanilla_id)
    for sub in ctx.subraces.values():
        race_assignments[sub.name] = sub.furry_id
        furry_to_vanilla.setdefault(sub.furry_id, []).append(sub.name)

    count = furrify_all_schlongs(
        list(plugin_set), patch, race_assignments, furry_to_vanilla,
        furry.races)
    assert count > 0, "No SOS quests were furrified"

    return patch, furry.races


# -- Helpers --


def _compat_race_edids(patch, races, compat_edid):
    """Get the set of race EditorIDs in a patched compat FLST.

    Reads raw LNAM FormIDs and matches them against race records
    denormalized to the patch's master-list space.
    """
    flst = None
    for rec in patch.get_records_by_signature('FLST'):
        if rec.editor_id == compat_edid:
            flst = rec
            break
    if flst is None:
        return set()

    lnam_fids = {sr.get_uint32() for sr in flst.subrecords
                 if sr.signature == 'LNAM'}

    # Build reverse lookup: patch-local FormID -> EditorID
    local_to_edid = {}
    for edid, rec in races.items():
        norm_fid = rec.normalize_form_id(rec.form_id)
        local_fid = patch.denormalize_form_id(norm_fid)
        local_to_edid[local_fid] = edid

    return {local_to_edid[fid] for fid in lnam_fids if fid in local_to_edid}


def _flst_len(patch, editor_id):
    """Count LNAM entries in a patched FLST."""
    for rec in patch.get_records_by_signature('FLST'):
        if rec.editor_id == editor_id:
            return sum(1 for sr in rec.subrecords if sr.signature == 'LNAM')
    return -1


# -- Tests --


CAT_COMPAT = "YASCatSheath_CompatibleRaces"
DOG_COMPAT = "YASDogSheathMale_CompatibleRaces"


class TestFelineSchlongs:
    """Feline schlong compat list (YASCatSheath) should include
    SnowElfRace and the YASWinterholdRace subrace."""

    def test_shan_furry_race_present(self, schlong_result):
        """YASShanRace should remain in the compat list."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, CAT_COMPAT)
        assert 'YASShanRace' in edids

    def test_snow_elf_added(self, schlong_result):
        """SnowElfRace (furrified to YASShanRace) should be added."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, CAT_COMPAT)
        assert 'SnowElfRace' in edids

    def test_winterhold_subrace_added(self, schlong_result):
        """YASWinterholdRace subrace (furry=YASShanRace) should be added."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, CAT_COMPAT)
        assert 'YASWinterholdRace' in edids

    def test_parallel_lists_same_length(self, schlong_result):
        """CompatibleRaces, RaceProbabilities, and RaceSizes must have
        the same number of entries."""
        patch, _ = schlong_result
        stem = "YASCatSheath"
        compat_len = _flst_len(patch, f"{stem}_CompatibleRaces")
        prob_len = _flst_len(patch, f"{stem}_RaceProbabilities")
        size_len = _flst_len(patch, f"{stem}_RaceSizes")
        assert compat_len > 0, f"{stem} compat list empty or missing"
        assert compat_len == prob_len, \
            f"{stem} compat({compat_len}) != prob({prob_len})"
        assert compat_len == size_len, \
            f"{stem} compat({compat_len}) != size({size_len})"


class TestCanineSchlongs:
    """Canine schlong compat list (YASDogSheathMale) should include
    furrified vanilla races and the YASReachmanRace subrace."""

    def test_lykaios_present(self, schlong_result):
        """YASLykaiosRace (canine Nord) should remain in the compat list."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, DOG_COMPAT)
        assert 'YASLykaiosRace' in edids

    def test_nord_added(self, schlong_result):
        """NordRace (furrified to YASLykaiosRace) should be added."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, DOG_COMPAT)
        assert 'NordRace' in edids

    def test_breton_added(self, schlong_result):
        """BretonRace (furrified to YASKygarraRace) should be added."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, DOG_COMPAT)
        assert 'BretonRace' in edids

    def test_reachman_subrace_added(self, schlong_result):
        """YASReachmanRace subrace (furry=YASKonoiRace) should be added."""
        patch, races = schlong_result
        edids = _compat_race_edids(patch, races, DOG_COMPAT)
        assert 'YASReachmanRace' in edids

    def test_parallel_lists_same_length(self, schlong_result):
        """CompatibleRaces, RaceProbabilities, and RaceSizes must have
        the same number of entries."""
        patch, _ = schlong_result
        stem = "YASDogSheathMale"
        compat_len = _flst_len(patch, f"{stem}_CompatibleRaces")
        prob_len = _flst_len(patch, f"{stem}_RaceProbabilities")
        size_len = _flst_len(patch, f"{stem}_RaceSizes")
        assert compat_len > 0, "Canine compat list empty or missing"
        assert compat_len == prob_len, \
            f"canine compat({compat_len}) != prob({prob_len})"
        assert compat_len == size_len, \
            f"canine compat({compat_len}) != size({size_len})"
