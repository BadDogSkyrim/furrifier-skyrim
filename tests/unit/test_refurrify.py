"""Re-furrification helpers — see PLAN_FURRIFIER_REFURRIFY.md.

Detection is by **plugin identity**, not RNAM: at save time the patch's
TES4 CNAM (author) is stamped with `FURRIFIER_AUTHOR_PREFIX + version`,
and future runs treat any plugin whose author starts with that prefix
as prior furrifier output. The earlier RNAM-based approach missed
normal-race assignments (whose RNAM stays at the vanilla race) and
subrace assignments (whose RNAM points at a new patch-created EDID
that wasn't in the tracked set).

`furrifier_patch_names` builds the set of qualifying plugin filenames
from a PluginSet. `is_furrified` returns True if ANY record in an NPC's
override chain came from one of those plugins. `find_pre_furry_record`
walks the chain top-to-bottom and returns the first record whose source
plugin isn't a furrifier patch — the source for rebuild-mode re-derivation.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from furrifier.context import (
    FURRIFIER_AUTHOR_PREFIX, find_pre_furry_record, furrifier_patch_names,
    is_furrified,
)


def _stub_plugin(name: str, author: str = ""):
    """Plugin stub with the minimum attrs the helpers touch."""
    plugin = MagicMock()
    plugin.file_path = Path(name)
    plugin.header.author = author
    return plugin


def _stub_record(plugin):
    """Record stub keyed to a plugin; `.plugin` is what detection reads."""
    rec = MagicMock()
    rec.plugin = plugin
    return rec


def _stub_npc(obj_id: int):
    """NPC stub. normalize_form_id is identity-on-value (production code
    would resolve master-list to load-order indexing; tests don't need
    that as long as get_override_chain accepts the same value back)."""
    rec = MagicMock()
    rec.signature = 'NPC_'
    rec.form_id = MagicMock(value=obj_id)
    rec.normalize_form_id.side_effect = lambda fid: MagicMock(value=fid.value)
    return rec


class TestFurrifierPatchNames:
    def test_collects_prefixed_authors(self):
        plugins = [
            _stub_plugin('Skyrim.esm', author='Bethesda'),
            _stub_plugin('YASNPCPatch01.esp',
                         author=f'{FURRIFIER_AUTHOR_PREFIX} 1.2.0'),
        ]
        plugin_set = MagicMock()
        plugin_set.__iter__.return_value = iter(plugins)
        assert furrifier_patch_names(plugin_set) == {'yasnpcpatch01.esp'}

    def test_empty_author_excluded(self):
        plugins = [_stub_plugin('Update.esm', author='')]
        plugin_set = MagicMock()
        plugin_set.__iter__.return_value = iter(plugins)
        assert furrifier_patch_names(plugin_set) == set()

    def test_lowercase_normalization(self):
        plugins = [_stub_plugin(
            'YASNPCPatch.ESP', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.2.0')]
        plugin_set = MagicMock()
        plugin_set.__iter__.return_value = iter(plugins)
        assert furrifier_patch_names(plugin_set) == {'yasnpcpatch.esp'}

    def test_prefix_match_tolerates_version_suffix(self):
        """Any version, any whitespace — startswith() is the contract."""
        plugins = [
            _stub_plugin('A.esp', author=FURRIFIER_AUTHOR_PREFIX),
            _stub_plugin('B.esp', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.0.0'),
            _stub_plugin('C.esp', author=f'{FURRIFIER_AUTHOR_PREFIX}-dev'),
        ]
        plugin_set = MagicMock()
        plugin_set.__iter__.return_value = iter(plugins)
        assert furrifier_patch_names(plugin_set) == {
            'a.esp', 'b.esp', 'c.esp'}

    def test_missing_file_path_excluded(self):
        """In-memory plugins without a file_path can't be name-matched."""
        plugin = MagicMock()
        plugin.file_path = None
        plugin.header.author = f'{FURRIFIER_AUTHOR_PREFIX} 1.0.0'
        plugin_set = MagicMock()
        plugin_set.__iter__.return_value = iter([plugin])
        assert furrifier_patch_names(plugin_set) == set()


class TestIsFurrified:
    """The regression case the old RNAM-based detection missed.

    A Nord NPC furrified by a prior patch keeps RNAM=NordRace — only
    the patched NordRace record carries the furry head data. RNAM-based
    detection saw a vanilla race form id and returned False, so the
    next run re-furrified the NPC. Plugin-identity detection catches
    it because the override comes from a CNAM-marked plugin.
    """

    def test_nord_npc_with_unchanged_rnam_detected_via_chain(self):
        skyrim = _stub_plugin('Skyrim.esm', author='Bethesda')
        patch = _stub_plugin(
            'YASNPCPatch01.esp', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.2.0')
        vanilla_rec = _stub_record(skyrim)
        patch_rec = _stub_record(patch)
        chain = [vanilla_rec, patch_rec]
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = chain
        npc = _stub_npc(0x00013466)
        assert is_furrified(plugin_set, npc, {'yasnpcpatch01.esp'}) is True

    def test_no_furry_plugin_in_chain_returns_false(self):
        skyrim = _stub_plugin('Skyrim.esm', author='Bethesda')
        ussep = _stub_plugin('USSEP.esp', author='USSEP Team')
        chain = [_stub_record(skyrim), _stub_record(ussep)]
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = chain
        npc = _stub_npc(0x00013466)
        assert is_furrified(plugin_set, npc, {'yasnpcpatch01.esp'}) is False

    def test_chain_lookup_misses_returns_false(self):
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = None
        npc = _stub_npc(0x00013466)
        assert is_furrified(plugin_set, npc, {'yasnpcpatch01.esp'}) is False

    def test_winner_overridden_by_non_furry_still_detects_prior_furry(self):
        """Chain: vanilla → prior_furry → later_non_furry. The later
        non-furry override wins, but the NPC still went through a
        furrifier patch — preserve mode must catch it."""
        skyrim = _stub_plugin('Skyrim.esm', author='Bethesda')
        patch = _stub_plugin(
            'YASNPCPatch01.esp', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.2.0')
        new_mod = _stub_plugin('SomeNPCMod.esp', author='ModAuthor')
        chain = [_stub_record(skyrim), _stub_record(patch),
                 _stub_record(new_mod)]
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = chain
        npc = _stub_npc(0x00013466)
        assert is_furrified(plugin_set, npc, {'yasnpcpatch01.esp'}) is True

    def test_record_without_plugin_is_skipped_gracefully(self):
        rec = MagicMock()
        rec.plugin = None
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = [rec]
        npc = _stub_npc(0x00013466)
        assert is_furrified(plugin_set, npc, {'yasnpcpatch01.esp'}) is False


class TestFindPreFurryRecord:
    def test_returns_first_non_furry_record_from_winner(self):
        """Walk-back is winner→base; for [vanilla, ussep, furry_winner]
        the topmost non-furry record is ussep, not vanilla."""
        skyrim = _stub_plugin('Skyrim.esm', author='Bethesda')
        ussep = _stub_plugin('USSEP.esp', author='USSEP Team')
        patch = _stub_plugin(
            'YASNPCPatch01.esp', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.2.0')
        vanilla_rec = _stub_record(skyrim)
        ussep_rec = _stub_record(ussep)
        patch_rec = _stub_record(patch)
        chain = [vanilla_rec, ussep_rec, patch_rec]
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = chain
        npc = _stub_npc(0x00013466)

        result = find_pre_furry_record(
            plugin_set, npc, {'yasnpcpatch01.esp'})
        assert result is ussep_rec

    def test_no_furry_in_chain_returns_winner(self):
        skyrim = _stub_plugin('Skyrim.esm', author='Bethesda')
        ussep = _stub_plugin('USSEP.esp', author='USSEP Team')
        vanilla_rec = _stub_record(skyrim)
        ussep_rec = _stub_record(ussep)
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = [vanilla_rec, ussep_rec]
        npc = _stub_npc(0x00013466)

        result = find_pre_furry_record(
            plugin_set, npc, {'yasnpcpatch01.esp'})
        assert result is ussep_rec

    def test_all_furry_chain_returns_none(self):
        patch1 = _stub_plugin(
            'YASNPCPatch01.esp', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.0.0')
        patch2 = _stub_plugin(
            'YASNPCPatch02.esp', author=f'{FURRIFIER_AUTHOR_PREFIX} 1.2.0')
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = [
            _stub_record(patch1), _stub_record(patch2)]
        npc = _stub_npc(0x00013466)

        result = find_pre_furry_record(
            plugin_set, npc, {'yasnpcpatch01.esp', 'yasnpcpatch02.esp'})
        assert result is None

    def test_no_chain_returns_none(self):
        plugin_set = MagicMock()
        plugin_set.get_override_chain.return_value = None
        npc = _stub_npc(0x00013466)
        result = find_pre_furry_record(
            plugin_set, npc, {'yasnpcpatch01.esp'})
        assert result is None
