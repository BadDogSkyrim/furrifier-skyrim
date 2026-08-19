"""`--armor` and `--schlongs` partition the armor space on biped slot 52.

Runs the real armor pass over a load order holding both kinds of addon --
vanilla helmets, hoods and circlets from Skyrim.esm and the DLCs, sheath
ARMAs from the four schlong plugins -- once per mask, and checks that
each mask touches only its own half.

This is the behaviour the SFW/NSFW package split rests on. The SFW patch
must carry no sheath records (it ships without the schlong plugins, so a
sheath override in it is a missing-master waiting to happen), and the
NSFW patch must carry nothing but -- before the split it shipped 68
generic armor ARMAs that duplicated the SFW patch's own work.

Reuses test_schlong_furrification's load order so the list of plugins,
and the reasons each one is on it, live in one place.
"""

import pytest

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.context import (
    FURRIFIABLE_BODYPARTS, FurryContext, armor_bodypart_mask,
)
from furrifier.furry_load import (
    load_races, load_headparts, build_race_headparts, build_race_tints)
from furrifier.models import Bodypart
from furrifier.race_defs import load_scheme
from furrifier.util import get_bodypart_flags
from furrifier.vanilla_setup import setup_vanilla

from conftest import plugins_available
from test_schlong_furrification import SCHLONG_PLUGINS

pytestmark = pytest.mark.skipif(
    not plugins_available(SCHLONG_PLUGINS),
    reason=f"required plugins missing: {SCHLONG_PLUGINS}",
)

MASK_ARMOR = armor_bodypart_mask(True, False)
MASK_SCHLONG = armor_bodypart_mask(False, True)
MASK_BOTH = armor_bodypart_mask(True, True)


# -- Fixtures --


@pytest.fixture(scope="module")
def data_dir():
    d = find_game_data('tes5')
    if d is None:
        pytest.skip("Skyrim data files not found")
    return d


@pytest.fixture(scope="module")
def ctx():
    c = load_scheme('all_races_test')
    setup_vanilla(c)
    return c


@pytest.fixture(scope="module")
def plugin_set(data_dir):
    lo = LoadOrder.from_list(SCHLONG_PLUGINS, data_dir=data_dir,
                             game_id='tes5')
    ps = PluginSet(lo)
    strings_dir = find_strings_dir()
    if strings_dir:
        ps.string_search_dirs = [str(strings_dir)]
    ps.load_all()
    return ps


def _run_armor_pass(plugin_set, ctx, data_dir, bodypart_mask, patch_name):
    """Fresh patch -> races -> merge -> furrify armor, at one mask.

    Each mask needs its own patch: the passes copy records into it, so
    sharing one would let an earlier mask's overrides stand in as a later
    mask's input. Nothing is saved -- new_plugin only names the path.
    """
    patch = Plugin.new_plugin(data_dir / patch_name)
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

    merged = furry.merge_armor_overrides(
        plugin_set, bodypart_mask=bodypart_mask)
    modified = furry.furrify_all_armor(
        plugin_set, bodypart_mask=bodypart_mask)
    return patch, merged, modified


@pytest.fixture(scope="module")
def masked_runs(plugin_set, ctx, data_dir):
    """The armor pass at all three masks, keyed by which flags were set."""
    return {
        'armor': _run_armor_pass(plugin_set, ctx, data_dir, MASK_ARMOR,
                                 'ArmorOnlyTEST.esp'),
        'schlongs': _run_armor_pass(plugin_set, ctx, data_dir, MASK_SCHLONG,
                                    'SchlongOnlyTEST.esp'),
        'both': _run_armor_pass(plugin_set, ctx, data_dir, MASK_BOTH,
                                'ArmorAndSchlongTEST.esp'),
    }


# -- Helpers --


def _armas_by_slot(patch):
    """(slot-52, everything-else) ARMA EditorIDs written into `patch`.

    furrify_all_armor copies exactly the addons it changed, so the
    patch's ARMA set *is* the set of records the pass touched.
    """
    slot52, other = set(), set()
    for rec in patch.get_records_by_signature('ARMA'):
        name = rec.editor_id or f'<{rec.form_id.value:08X}>'
        if get_bodypart_flags(rec) & Bodypart.SCHLONG:
            slot52.add(name)
        else:
            other.add(name)
    return slot52, other


def _describe(names, limit=8):
    listed = sorted(names)[:limit]
    more = len(names) - len(listed)
    return ', '.join(listed) + (f' (+{more} more)' if more else '')


# -- Tests --


class TestArmorHalf:
    """--armor without --schlongs: everything but slot 52."""

    def test_touches_ordinary_armor(self, masked_runs):
        patch, _, _ = masked_runs['armor']
        _, other = _armas_by_slot(patch)
        assert other, ("--armor furrified no ordinary armor addons; the "
                       "load order or scheme has gone inert and the "
                       "slot-52 assertion below would pass vacuously")

    def test_touches_no_sheath_addon(self, masked_runs):
        """The assertion the SFW package depends on."""
        patch, _, _ = masked_runs['armor']
        slot52, _ = _armas_by_slot(patch)
        assert not slot52, (
            f"--armor without --schlongs modified {len(slot52)} slot-52 "
            f"addon(s): {_describe(slot52)}")


class TestSchlongHalf:
    """--schlongs without --armor: slot 52 and nothing else."""

    def test_touches_sheath_addons(self, masked_runs):
        patch, _, _ = masked_runs['schlongs']
        slot52, _ = _armas_by_slot(patch)
        assert slot52, ("--schlongs furrified no sheath addons; the schlong "
                        "plugins are loaded but produced nothing, so the "
                        "exclusion assertion below would pass vacuously")

    def test_touches_no_ordinary_armor(self, masked_runs):
        """The assertion the NSFW package depends on."""
        patch, _, _ = masked_runs['schlongs']
        _, other = _armas_by_slot(patch)
        assert not other, (
            f"--schlongs without --armor modified {len(other)} non-slot-52 "
            f"addon(s): {_describe(other)}")


class TestBothHalves:
    """Both flags: the unsplit behaviour, unchanged."""

    def test_mask_equals_the_unsplit_constant(self):
        """Both halves together must be bit-identical to the mask every
        caller used before the split, or the default path has drifted."""
        assert MASK_BOTH == int(FURRIFIABLE_BODYPARTS)

    def test_covers_both_kinds_of_addon(self, masked_runs):
        patch, _, _ = masked_runs['both']
        slot52, other = _armas_by_slot(patch)
        assert slot52, "both flags set but no sheath addon was touched"
        assert other, "both flags set but no ordinary armor was touched"

    def test_each_half_is_a_subset_of_the_whole_by_kind(self, masked_runs):
        """Per-kind, not per-record. The claiming contest runs over an
        ARMO's in-mask addons in order, so widening the mask can hand a
        vanilla race to an addon that a narrower run gave to another --
        the record sets are legitimately not nested. What must hold is
        that neither half invents a kind of addon the full run lacks.
        """
        armor_slot52, armor_other = _armas_by_slot(masked_runs['armor'][0])
        schlong_slot52, schlong_other = _armas_by_slot(
            masked_runs['schlongs'][0])
        both_slot52, both_other = _armas_by_slot(masked_runs['both'][0])

        assert not armor_slot52 and not schlong_other
        assert armor_other and both_other
        assert schlong_slot52 and both_slot52


class TestArmoScoping:
    """The ARMO merge is scoped too, or the patch keeps the bloat.

    Only ARMOs with two or more overrides are merge candidates at all, so
    how much the scope check removes depends on the load order. On this
    one every candidate carries ordinary furrifiable addons, so the armor
    half drops nothing and the schlong half drops all 52 -- there is no
    furrifier patch among the masters here to give the sheath ARMOs a
    second override. Assert the invariants, not that particular split.
    """

    def test_scoping_never_merges_more_than_the_full_mask(self, masked_runs):
        _, merged_armor, _ = masked_runs['armor']
        _, merged_schlongs, _ = masked_runs['schlongs']
        _, merged_both, _ = masked_runs['both']
        assert merged_armor <= merged_both
        assert merged_schlongs <= merged_both

    def test_schlong_only_merges_fewer_armos_than_both(self, masked_runs):
        """Scoping the merge is what actually shrinks the NSFW patch.
        Without it, furrify_all_armor would ignore the out-of-mask ARMOs
        but merge_armor_overrides would still copy every one of them in.
        """
        _, merged_schlongs, _ = masked_runs['schlongs']
        _, merged_both, _ = masked_runs['both']
        assert merged_schlongs < merged_both, (
            f"schlongs-only merged {merged_schlongs} ARMOs, both merged "
            f"{merged_both} -- the bodypart mask is not reaching the merge")

    @pytest.mark.parametrize("key,mask", [
        ('armor', MASK_ARMOR),
        ('schlongs', MASK_SCHLONG),
    ])
    def test_every_merged_armo_is_in_scope(self, masked_runs, key, mask):
        """No merged ARMO may be in the patch purely on the strength of
        addons the run was told to ignore."""
        patch, _, _ = masked_runs[key]
        armas = {}
        for plugin in patch.plugin_set:
            for arma in plugin.get_records_by_signature('ARMA'):
                armas[arma.normalize_form_id(arma.form_id).value] = arma

        offenders = []
        for armo in patch.get_records_by_signature('ARMO'):
            flags = []
            for sr in armo.get_subrecords('MODL'):
                if sr.size < 4:
                    continue
                arma = armas.get(
                    armo.normalize_form_id(sr.get_form_id()).value)
                if arma is not None:
                    flags.append(get_bodypart_flags(arma))
            furrifiable = [f for f in flags if f & FURRIFIABLE_BODYPARTS]
            if furrifiable and not any(f & mask for f in furrifiable):
                offenders.append(armo.editor_id or
                                 f'<{armo.form_id.value:08X}>')

        assert not offenders, (
            f"{len(offenders)} ARMO(s) in a {key}-only patch carry only "
            f"out-of-mask furrifiable addons: {_describe(offenders)}")
