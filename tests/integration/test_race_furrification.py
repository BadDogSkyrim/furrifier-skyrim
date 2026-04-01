"""Integration tests for race furrification.

Ported from BDFurrySkyrimTEST.pas TestRaces procedure.
Each test furrifies a vanilla race by copying skin/armor-race from the
furry race template, then verifies the result after save/reload.
"""

import pytest

from conftest import (
    requires_gamefiles, find_record, find_by_formid, run_verify_phase,
)


pytestmark = requires_gamefiles


def _resolve_formid_edid(form_id, patch_plugin, source_plugins):
    """Resolve a FormID from a patch plugin to its EditorID.

    Uses the patch's master list to determine which source plugin
    owns the record, then looks it up via the plugin's FormID index.
    """
    masters = patch_plugin.header.masters
    if form_id.file_index >= len(masters):
        return None

    master_name = masters[form_id.file_index].lower()

    for plugin in source_plugins:
        if plugin.file_path and plugin.file_path.name.lower() == master_name:
            # In this plugin, local records use file_index = len(masters)
            local_idx = len(plugin.header.masters)
            local_fid = (local_idx << 24) | form_id.object_index
            rec = plugin.get_record_by_form_id(local_fid)
            if rec:
                return rec.editor_id
            # Also try as a master reference (object from one of its masters)
            rec = plugin.get_record_by_form_id(form_id.value)
            return rec.editor_id if rec else None

    return None


class TestRaceFurrification:
    """Furrify races and verify results survive save/reload."""

    def test_furrify_nord_race(self, furrify_and_check, all_plugins,
                               races_by_edid):
        """Furrify all races; check NordRace skin, armor race, and head parts."""
        nord = races_by_edid.get('NordRace')
        assert nord is not None, "NordRace not found"
        form_id = nord.form_id

        khajiit, _ = find_record(all_plugins, 'RACE', 'KhajiitRace')
        assert khajiit is not None, "KhajiitRace not found"


        def write(furry_ctx):
            # furrify_all_races is called in the furry_ctx fixture;
            # just verify it ran.
            pass


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "NordRace not in saved plugin"

            # FULL should be delocalized to an inline string
            full = patched.get_subrecord('FULL')
            assert full is not None, "FULL missing"
            assert full.size > 4, \
                f"FULL still a 4-byte string ID ({full.data.hex()}), " \
                f"should be inline string"
            assert full.get_string() == 'Nord', \
                f"FULL should be 'Nord', got {full.get_string()!r}"

            # WNAM (skin) should point to YASLykaiosSkin
            wnam = patched.get_subrecord('WNAM')
            assert wnam is not None, "WNAM missing"
            wnam_edid = _resolve_formid_edid(
                wnam.get_form_id(), reloaded, all_plugins)
            assert wnam_edid == 'YASLykaiosSkin', \
                f"WNAM should be YASLykaiosSkin, got {wnam_edid}"

            # RNAM (armor race) should point to KhajiitRace
            rnam = patched.get_subrecord('RNAM')
            assert rnam is not None, "RNAM missing on furrified race"
            rnam_edid = _resolve_formid_edid(
                rnam.get_form_id(), reloaded, all_plugins)
            assert rnam_edid == 'KhajiitRace', \
                f"RNAM should be KhajiitRace, got {rnam_edid}"

            # HEAD subrecords should be from the furry race
            heads = patched.get_subrecords('HEAD')
            assert len(heads) > 0, "No HEAD subrecords on furrified race"

            head_edids = []
            for h in heads:
                edid = _resolve_formid_edid(
                    h.get_form_id(), reloaded, all_plugins)
                if edid:
                    head_edids.append(edid)

            assert any('Lykaios' in e for e in head_edids), \
                f"Expected a Lykaios head part, got: {head_edids}"

        furrify_and_check(write, verify)


# ===================================================================
# Verify phase: save, reload, run all deferred verify callbacks
# This MUST be the last test in this file.
# ===================================================================


def test_verify_saved_races(patch):
    """Save the patch, reopen it, run all deferred verify callbacks."""
    failures = run_verify_phase(patch)
    if failures:
        pytest.fail(
            f"{len(failures)} verify failure(s) after save/reload:\n"
            + "\n".join(failures)
        )
