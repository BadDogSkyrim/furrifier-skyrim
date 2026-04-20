"""Integration test: Orc NPCs furrified to Mino get diverse horn
headparts, not all the same one.

Regression test for the "all minos get steer horns" bug. Root cause was
that BDUngulates mino horn HDPT records have DATA flags=0x01 (Playable
only, neither Male 0x02 nor Female 0x04), so build_race_headparts was
dropping them from the candidate index. With no candidates, the matcher
returned None and the game fell back to the race's single default
headpart for every NPC.

Uses its own plugin_set (loads BDUngulates.esp) and the ungulate_test
scheme (OrcRace -> BDMinoRace) so the shipping data can evolve without
breaking this regression gate.
"""

import pytest

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.context import FurryContext
from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla
from furrifier.furry_load import (
    load_races, load_headparts, build_race_headparts, build_race_tints)
from furrifier.models import HeadpartType


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


def _find_data_dir():
    return find_game_data('tes5')


from conftest import plugins_available

requires_mino_files = pytest.mark.skipif(
    not plugins_available(MINO_PLUGINS),
    reason=f"required plugins missing: {MINO_PLUGINS}",
)

pytestmark = requires_mino_files


@pytest.fixture(scope="module")
def data_dir():
    d = _find_data_dir()
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
def mino_furry(mino_plugin_set, data_dir):
    ctx = load_scheme('ungulate_test')
    setup_vanilla(ctx)

    races_by_edid_info = load_races(mino_plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid_info.items()}
    headparts = load_headparts(mino_plugin_set, ctx)
    race_headparts = build_race_headparts(list(mino_plugin_set), headparts)
    race_tints = build_race_tints(list(mino_plugin_set))

    patch = Plugin.new_plugin(data_dir / 'MinoTEST.esp')
    patch.plugin_set = mino_plugin_set
    furry = FurryContext(
        patch=patch, ctx=ctx, races=races,
        all_headparts=headparts, race_headparts=race_headparts,
        race_tints=race_tints, plugin_set=mino_plugin_set)
    furry.furrify_all_races()
    return furry


def _eyebrow_edids(patched_npc, all_headparts):
    """Extract the EditorIDs of EYEBROWS headparts assigned via PNAM."""
    edids = []
    for sr in patched_npc.get_subrecords('PNAM'):
        obj_id = sr.get_uint32() & 0x00FFFFFF
        for hp_id, hp in all_headparts.items():
            if (hp.record
                    and hp.hp_type == HeadpartType.EYEBROWS
                    and (hp.record.form_id.value & 0x00FFFFFF) == obj_id):
                edids.append(hp_id)
                break
    return edids


class TestMinoHorns:
    """Three male Orc NPCs furrified to Mino should each get an
    eyebrow (horn) PNAM, and they should not all be the same horn."""


    @pytest.fixture(scope="class")
    def mino_horns(self, mino_furry, mino_plugin_set):
        """Run furrify_npc on the three test Orcs and collect their
        assigned eyebrow EditorIDs."""
        result = {}
        for name in ('Borkul', 'Dushnamub', 'EncBandit06Melee1HOrcM'):
            npc = mino_plugin_set.get_record_by_edid('NPC_', name)
            assert npc is not None, f"{name} not found in plugin set"
            patched = mino_furry.furrify_npc(npc)
            assert patched is not None, f"{name} should be furrifiable"
            brow_edids = _eyebrow_edids(patched, mino_furry.all_headparts)
            result[name] = brow_edids
        return result


    def test_borkul_gets_a_horn(self, mino_horns):
        """Borkul must always get a horn — male mino EYEBROWS = 1.0."""
        assert len(mino_horns['Borkul']) == 1, \
            f"Borkul should have exactly one eyebrow PNAM, got {mino_horns['Borkul']}"


    def test_dushnamub_gets_a_horn(self, mino_horns):
        assert len(mino_horns['Dushnamub']) == 1, \
            f"Dushnamub should have exactly one eyebrow PNAM, got {mino_horns['Dushnamub']}"


    def test_bandit_orc_gets_a_horn(self, mino_horns):
        assert len(mino_horns['EncBandit06Melee1HOrcM']) == 1, \
            f"EncBandit06Melee1HOrcM should have exactly one eyebrow PNAM, " \
            f"got {mino_horns['EncBandit06Melee1HOrcM']}"


    def test_horns_are_not_all_identical(self, mino_horns):
        """If all three orcs get the same horn, selection is broken
        (either only one horn in the pool or hash_string isn't spreading
        NPCs)."""
        horns = {name: (edids[0] if edids else None)
                 for name, edids in mino_horns.items()}
        distinct = set(v for v in horns.values() if v is not None)
        assert len(distinct) > 1, (
            f"All three orcs got the same horn: {horns}. "
            f"Selection isn't spreading NPCs across multiple horns."
        )
