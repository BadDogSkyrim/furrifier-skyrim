"""Integration tests for leveled-list NPC extension.

Loads real Skyrim plugins, runs the full furrification pipeline through
extend_leveled_npcs, and verifies that:
- New NPCs were created and added to the patch
- Their RNAM points at a configured target furry race
- LVLN records gained matching LVLO entries (with preserved level)
- LLCT was bumped accordingly
"""

import struct

import pytest

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.context import FurryContext
from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla
from furrifier.furry_load import (
    load_races, load_headparts, build_race_headparts, build_race_tints)


def _find_data_dir():
    return find_game_data('tes5')


PLUGINS = [
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
    "BDCatRaces.esp",
    "YASCanineRaces.esp",
    "BDUngulates.esp",
    "CellanRace.esp",
]

from conftest import plugins_available

requires_files = pytest.mark.skipif(
    not plugins_available(PLUGINS),
    reason=f"required plugins missing: {PLUGINS}",
)
pytestmark = requires_files


@pytest.fixture(scope="module")
def data_dir():
    d = _find_data_dir()
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
    lo = LoadOrder.from_list(PLUGINS, data_dir=data_dir, game_id='tes5')
    ps = PluginSet(lo)
    strings_dir = find_strings_dir()
    if strings_dir:
        ps.string_search_dirs = [str(strings_dir)]
    ps.load_all()
    return ps


@pytest.fixture(scope="module")
def leveled_result(plugin_set, ctx, data_dir):
    """Run race + NPC + leveled-list furrification, save, reload.

    Returns (reloaded_patch, in_memory_patch, furry, new_count, list_count).
    The reloaded copy catches save-time bugs (stale fixup indices,
    missing masters, dangling FormID references). The in-memory patch
    is kept around for tests that need access to the original NPC
    records and races dict.
    """
    patch = Plugin.new_plugin('c:/tmp/LeveledTEST.esp')
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
    # Furrify NPCs first so leveled extension runs against the same
    # state as production (existing patch overrides for vanilla NPCs).
    furry.furrify_all_npcs(list(plugin_set))
    new_count, list_count = furry.extend_leveled_npcs(list(plugin_set))
    assert new_count > 0, "No leveled-list NPCs were created"
    assert list_count > 0, "No LVLNs were extended"

    patch.save()
    reloaded = Plugin.load('c:/tmp/LeveledTEST.esp')
    reloaded.plugin_set = plugin_set

    return reloaded, patch, furry, new_count, list_count


def _race_edid_for_rnam(rnam_fid_value, races, patch):
    """Look up race EditorID by raw FormID (handles patch-local denormalize)."""
    for edid, rec in races.items():
        norm = rec.normalize_form_id(rec.form_id)
        local = patch.denormalize_form_id(norm)
        if local == rnam_fid_value:
            return edid
    return None


class TestLeveledNpcCreation:

    def test_some_npcs_created(self, leveled_result):
        _, _, _, new_count, _ = leveled_result
        assert new_count > 0

    def test_some_lists_extended(self, leveled_result):
        _, _, _, _, list_count = leveled_result
        assert list_count > 0

    def test_new_npcs_have_yas_prefix(self, leveled_result):
        _, patch, _, _, _ = leveled_result
        npcs = list(patch.get_records_by_signature('NPC_'))
        yas = [n for n in npcs if (n.editor_id or '').startswith('YAS_')]
        assert len(yas) > 0, \
            f"Expected YAS_-prefixed NPCs, got {[n.editor_id for n in npcs[:5]]}"

    def test_new_npcs_target_configured_races(self, leveled_result):
        _, patch, furry, _, _ = leveled_result
        targets = {'YASLykaiosRace', 'YASKonoiRace'}
        seen_targets: set[str] = set()
        for n in patch.get_records_by_signature('NPC_'):
            if not (n.editor_id or '').startswith('YAS_'):
                continue
            rnam = n.get_subrecord('RNAM')
            if rnam is None:
                continue
            edid = _race_edid_for_rnam(
                rnam.get_uint32(), furry.races, patch)
            if edid in targets:
                seen_targets.add(edid)
        assert seen_targets, "No new NPCs had a target furry race RNAM"
        assert seen_targets <= targets, \
            f"Unexpected races on new NPCs: {seen_targets - targets}"


class TestLeveledNpcLists:

    def test_extended_lvln_has_more_entries(self, leveled_result, plugin_set):
        """At least one extended LVLN should have more LVLO entries than its source."""
        _, patch, _, _, _ = leveled_result

        skyrim = next(p for p in plugin_set
                      if p.file_path and p.file_path.name == 'Skyrim.esm')
        source_counts: dict[str, int] = {}
        for lvln in skyrim.get_records_by_signature('LVLN'):
            eid = lvln.editor_id or ''
            source_counts[eid] = sum(
                1 for sr in lvln.subrecords if sr.signature == 'LVLO')

        grew = []
        for lvln in patch.get_records_by_signature('LVLN'):
            eid = lvln.editor_id or ''
            patched_count = sum(
                1 for sr in lvln.subrecords if sr.signature == 'LVLO')
            if patched_count > source_counts.get(eid, 0):
                grew.append((eid, source_counts.get(eid, 0), patched_count))
        assert grew, "Expected at least one LVLN to have more entries"

    def test_llct_matches_lvlo_count(self, leveled_result):
        """LLCT must match the actual LVLO count after patching."""
        _, patch, _, _, _ = leveled_result
        for lvln in patch.get_records_by_signature('LVLN'):
            llct = lvln.get_subrecord('LLCT')
            if llct is None:
                continue
            actual = sum(
                1 for sr in lvln.subrecords if sr.signature == 'LVLO')
            stored = llct.data[0] if llct.size >= 1 else 0
            # LLCT is U8; we cap at 255
            assert stored == min(actual, 255), \
                f"{lvln.editor_id}: LLCT={stored}, LVLO count={actual}"

    def test_vampire_source_yields_vampire_target(
            self, leveled_result, plugin_set):
        """Duplicates of vampire NPCs must use a vampire furry race.

        Walks vampire-source NPCs (race EditorID ends with 'Vampire')
        that appear in LVLNs, then verifies that any YAS_ duplicate
        derived from them targets a vampire variant.
        """
        _, patch, _, _, _ = leveled_result

        race_by_obj: dict[int, str] = {}
        for plugin in plugin_set:
            for r in plugin.get_records_by_signature('RACE'):
                race_by_obj[r.form_id.value & 0xffffff] = r.editor_id or ''

        # Build EDID lookup of source NPCs that are vampires
        vampire_npc_edids: set[str] = set()
        for plugin in plugin_set:
            for n in plugin.get_records_by_signature('NPC_'):
                rnam = n.get_subrecord('RNAM')
                if rnam is None:
                    continue
                race_eid = race_by_obj.get(rnam.get_form_id().object_index, '')
                if race_eid.endswith('Vampire') and n.editor_id:
                    vampire_npc_edids.add(n.editor_id)

        checked = 0
        for n in patch.get_records_by_signature('NPC_'):
            eid = n.editor_id or ''
            if not eid.startswith('YAS_'):
                continue
            # YAS_<src_edid>_<short_race> where short_race ends in 'V'
            # for vampire variants (per util.short_race_name).
            parts = eid[len('YAS_'):].rsplit('_', 1)
            if len(parts) != 2:
                continue
            src_edid, target_short = parts
            if src_edid not in vampire_npc_edids:
                continue
            assert target_short.endswith('V'), \
                f"Vampire source {src_edid!r} got non-vampire target " \
                f"{target_short!r}"
            checked += 1

        assert checked > 0, \
            "No vampire-source duplicates were created — test cannot " \
            "verify variant matching"

    def test_no_duplicate_lvlo_refs_per_list(self, leveled_result):
        """Within a single LVLN, each (source NPC, target race) pair
        should add at most one new LVLO entry, even if the source NPC
        appears multiple times in the vanilla list (different levels).
        Regression: SubCharBandit01Missile has ImperialM listed twice;
        before the fix, the furry duplicate was added twice."""
        _, patch, _, _, _ = leveled_result
        # Only check NPCs we created (YAS_*-prefixed); vanilla duplicates
        # within a list are legitimate (level scaling pattern).
        yas_objs = {
            n.form_id.value & 0xffffff
            for n in patch.get_records_by_signature('NPC_')
            if (n.editor_id or '').startswith('YAS_')
        }
        for lvln in patch.get_records_by_signature('LVLN'):
            seen: set[int] = set()
            for sr in lvln.subrecords:
                if sr.signature != 'LVLO' or sr.size < 12:
                    continue
                ref_obj = sr.get_uint32(4) & 0xffffff
                if ref_obj not in yas_objs:
                    continue
                assert ref_obj not in seen, \
                    f"{lvln.editor_id}: YAS_ NPC obj {ref_obj:#x} " \
                    f"added twice"
                seen.add(ref_obj)

    def test_first_match_wins_group(self, leveled_result):
        """A LVLN that matches an early group's substring should only get
        that group's races — not races from later groups.

        Test scheme: ['bandit'] group has Lykaios + Konoi; later catch-all
        has CellanRace. A bandit list should never get a CellanRace
        duplicate.
        """
        _, patch, _, _, _ = leveled_result
        bandit_lvln_obj_ids: set[int] = set()
        for lvln in patch.get_records_by_signature('LVLN'):
            if 'bandit' in (lvln.editor_id or '').lower():
                bandit_lvln_obj_ids.add(lvln.form_id.value & 0xffffff)

        # Find duplicates whose source NPC is in a bandit list and whose
        # target race is from a non-bandit group.
        # Easiest check: scan new entries on bandit LVLNs and verify no
        # entry references an NPC named with '_CellanRace'.
        bad: list[str] = []
        # Build NPC obj_id -> editor_id for patch NPCs
        npc_eid_by_obj: dict[int, str] = {}
        for n in patch.get_records_by_signature('NPC_'):
            npc_eid_by_obj[n.form_id.value & 0xffffff] = n.editor_id or ''

        for lvln in patch.get_records_by_signature('LVLN'):
            if 'bandit' not in (lvln.editor_id or '').lower():
                continue
            for sr in lvln.subrecords:
                if sr.signature != 'LVLO' or sr.size < 12:
                    continue
                ref_obj = sr.get_uint32(4) & 0xffffff
                eid = npc_eid_by_obj.get(ref_obj, '')
                # Catch-all uses CellanRace; short_race_name strips
                # the "Race" suffix → suffix is "_Cellan".
                if eid.startswith('YAS_') and eid.endswith('_Cellan'):
                    bad.append(f"{lvln.editor_id}: {eid}")
        assert not bad, \
            f"Bandit lists picked up catch-all races (first-match-wins " \
            f"violated): {bad[:5]}"

    def test_excluded_lvln_substrings_not_extended(
            self, leveled_result, plugin_set):
        """LVLNs whose editor_id matches a configured exclusion substring
        must not gain duplicates."""
        _, patch, _, _, _ = leveled_result
        exclusions = ("LCharOrc", "Thalmor", "Alikir", "Forsworn")

        # Source LVLO counts to compare against patched
        source_counts: dict[str, int] = {}
        for plugin in plugin_set:
            for lvln in plugin.get_records_by_signature('LVLN'):
                eid = lvln.editor_id or ''
                source_counts[eid] = sum(
                    1 for sr in lvln.subrecords if sr.signature == 'LVLO')

        for lvln in patch.get_records_by_signature('LVLN'):
            eid = lvln.editor_id or ''
            if not any(s in eid for s in exclusions):
                continue
            patched_count = sum(
                1 for sr in lvln.subrecords if sr.signature == 'LVLO')
            assert patched_count <= source_counts.get(eid, 0), \
                f"Excluded LVLN {eid!r} grew from " \
                f"{source_counts.get(eid)} to {patched_count} entries"

    def test_unique_npc_form_ids(self, leveled_result):
        """Patch-created NPC records must have unique FormIDs.

        Regression: an earlier bug copied each duplicate NPC twice
        (once explicitly, once via furrify_npc) leaving two records
        with the same FormID and producing dangling LVLO references."""
        _, patch, _, _, _ = leveled_result
        seen: dict[int, str] = {}
        for n in patch.get_records_by_signature('NPC_'):
            fid = n.form_id.value
            assert fid not in seen, \
                f"Duplicate NPC FormID {fid:#010x}: " \
                f"{seen[fid]!r} and {n.editor_id!r}"
            seen[fid] = n.editor_id or '?'

    def test_saved_lvln_refs_resolve(self, leveled_result, plugin_set):
        """After save+reload, every LVLO ref in a patched LVLN must
        resolve to a real record in the patch itself or in the master
        whose index it carries. Catches stale-master-index bugs."""
        reloaded, _, _, _, _ = leveled_result

        masters = reloaded.header.masters
        local_index = len(masters)

        # Build NPC obj_id sets per plugin (load-order-independent).
        npc_objs_by_plugin: dict[str, set[int]] = {}
        for plugin in plugin_set:
            name = plugin.file_path.name.lower() if plugin.file_path else ''
            npc_objs_by_plugin[name] = {
                n.form_id.value & 0xffffff
                for n in plugin.get_records_by_signature('NPC_')
            }
        patch_npc_objs = {
            n.form_id.value & 0xffffff
            for n in reloaded.get_records_by_signature('NPC_')
        }

        bad: list[tuple[str, int, str]] = []
        for lvln in reloaded.get_records_by_signature('LVLN'):
            for sr in lvln.subrecords:
                if sr.signature != 'LVLO' or sr.size < 12:
                    continue
                ref = sr.get_uint32(4)
                hi = (ref >> 24) & 0xff
                obj = ref & 0xffffff

                if hi == local_index:
                    if obj not in patch_npc_objs:
                        bad.append((lvln.editor_id or '?', ref, '<self>'))
                elif hi >= len(masters):
                    bad.append((lvln.editor_id or '?', ref, '<oob>'))
                else:
                    master_name = masters[hi].lower()
                    plugin_objs = npc_objs_by_plugin.get(master_name, set())
                    if obj not in plugin_objs:
                        bad.append(
                            (lvln.editor_id or '?', ref, masters[hi]))

        assert not bad, \
            f"Dangling LVLO refs after save: " \
            f"{[(e, f'{r:#010x}', src) for e, r, src in bad[:5]]}"

    def test_new_entries_reference_new_npcs(self, leveled_result):
        """LVLO entries we added must point at NPC_ records in the patch."""
        _, patch, _, _, _ = leveled_result

        patch_npc_objs = {
            n.form_id.value & 0xffffff
            for n in patch.get_records_by_signature('NPC_')
        }
        assert patch_npc_objs, "Patch has no NPC_ records"

        # For each patched LVLN, find LVLO entries that reference patch NPCs
        any_referenced = False
        for lvln in patch.get_records_by_signature('LVLN'):
            for sr in lvln.subrecords:
                if sr.signature != 'LVLO' or sr.size < 12:
                    continue
                ref_obj = sr.get_uint32(4) & 0xffffff
                if ref_obj in patch_npc_objs:
                    any_referenced = True
                    break
            if any_referenced:
                break
        assert any_referenced, \
            "No LVLN in patch references any patch-created NPC"
