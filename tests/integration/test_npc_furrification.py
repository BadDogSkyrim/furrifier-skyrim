"""Integration tests for NPC furrification.

Ported from BDFurrySkyrimTEST.pas TestNPCs procedure.
Each test loads a real NPC from Skyrim.esm, furrifies it, and verifies
the result after save/reload. All furrified NPCs accumulate in the shared
patch plugin (FurrifierTEST.esp) for inspection in xEdit.

Tests that modify records use furrify_and_check: the write callback runs
immediately, the verify callback is deferred until test_verify_saved_plugin
saves, reopens the file, and runs all verify callbacks.
"""

import struct

import pytest

from furrifier.models import Sex
from furrifier.vanilla_setup import unalias

from conftest import (
    requires_gamefiles, find_record, find_by_formid, run_verify_phase,
)


pytestmark = requires_gamefiles


def _get_race_edid(record, races_by_edid, plugin=None):
    """Get the EditorID of the race assigned to a patched NPC record.

    Checks the plugin's own records first (for patch-created subraces),
    then falls back to races_by_edid (source plugins).
    """
    rnam = record.get_subrecord('RNAM')
    if rnam is None:
        return None
    race_fid = rnam.get_uint32()
    # Check patch-created records first (subraces with new FormIDs)
    if plugin is not None:
        for rec in plugin.records:
            if rec.signature == 'RACE' and rec.form_id.value == race_fid:
                return rec.editor_id
    for edid, rec in races_by_edid.items():
        if rec.form_id.value == race_fid:
            return edid
    return None


def _assert_valid_formid(plugin, subrecord_sig, record, master_plugins):
    """Assert a FormID subrecord resolves to a real record.

    Checks that:
    1. The subrecord exists and is non-zero
    2. The master index byte points to a valid master
    3. The referenced record actually exists in that master
    """
    sr = record.get_subrecord(subrecord_sig)
    assert sr is not None, f"{subrecord_sig} subrecord missing"

    fid = sr.get_uint32()
    assert fid != 0, f"{subrecord_sig} FormID is null (0x00000000)"

    master_idx = (fid >> 24) & 0xFF
    masters = plugin.header.masters
    assert master_idx < len(masters), \
        f"{subrecord_sig} master index {master_idx} out of range " \
        f"(plugin has {len(masters)} masters)"

    # Verify the record exists in the referenced master
    object_id = fid & 0x00FFFFFF
    master_name = masters[master_idx]
    source = None
    for mp in master_plugins:
        if mp.file_path and mp.file_path.name.lower() == master_name.lower():
            source = mp
            break

    if source is not None:
        found = any(
            r.form_id.value & 0x00FFFFFF == object_id
            for r in source.records
            if r.signature == 'RACE'
        )
        assert found, \
            f"{subrecord_sig} FormID {fid:#010x} not found in {master_name}"


def _assert_subrecord_order(actual_sigs, expected_order):
    """Assert that subrecords appear in the expected relative order.

    expected_order is a list of signatures that must appear in that
    relative order within actual_sigs. Not every sig needs to be present,
    but those that are must not be out of order.
    """
    positions = {}
    for i, sig in enumerate(actual_sigs):
        if sig not in positions:
            positions[sig] = i

    prev_sig = None
    prev_pos = -1
    for sig in expected_order:
        if sig not in positions:
            continue
        pos = positions[sig]
        assert pos > prev_pos, \
            f"Subrecord {sig} (at {pos}) appears before {prev_sig} (at {prev_pos}) " \
            f"-- expected {prev_sig} before {sig}. " \
            f"Actual order: {actual_sigs}"
        prev_sig = sig
        prev_pos = pos


def _get_headpart_edids(record, all_headparts):
    """Get EditorIDs of all head parts on a patched NPC."""
    edids = []
    for sr in record.get_subrecords('PNAM'):
        obj_id = sr.get_uint32() & 0x00FFFFFF
        for hp in all_headparts.values():
            if hp.record and (hp.record.form_id.value & 0x00FFFFFF) == obj_id:
                edids.append(hp.editor_id)
                break
    return edids


def _has_headpart_containing(record, all_headparts, substring):
    """Check if any headpart EditorID contains the given substring."""
    for edid in _get_headpart_edids(record, all_headparts):
        if substring in edid:
            return True
    return False


def _count_headparts_containing(record, all_headparts, substring):
    """Count headpart EditorIDs containing the given substring."""
    return sum(1 for edid in _get_headpart_edids(record, all_headparts)
               if substring in edid)


def _tint_layer_count(record):
    """Count tint layers on the NPC (number of TINI subrecords)."""
    return len(record.get_subrecords('TINI'))


def _get_template_chain(npc, plugin_set):
    """Walk the TPLT chain and return the list of template NPCs."""
    chain = []
    current = npc
    while True:
        found = plugin_set.resolve_reference(current, 'TPLT')
        if found is None:
            break
        chain.append(found)
        current = found
    return chain


# ===================================================================
# Write+Verify tests (use furrify_and_check)
# ===================================================================


class TestNPCFurrification:
    """Furrify NPCs and verify results survive save/reload."""

    def test_balgruuf(self, furrify_and_check, all_plugins, races_by_edid,
                      all_headparts, race_tints):
        """Balgruuf: race stays NordRace, base data preserved."""
        npc, _ = find_record(all_plugins, 'NPC_', 'BalgruuftheGreater')
        assert npc is not None

        orig_acbs = npc.get_subrecord('ACBS').data[:]
        orig_aidt = npc.get_subrecord('AIDT').data[:]
        orig_dnam = npc.get_subrecord('DNAM').data[:]
        orig_full_name = npc.full_name
        orig_shrt = npc.get_subrecord('SHRT')
        orig_data = npc.get_subrecord('DATA').data[:] if npc.get_subrecord('DATA') else None
        form_id = npc.form_id

        assert orig_full_name is not None, "Balgruuf should have FULL name"
        assert orig_shrt is not None, "Balgruuf should have SHRT subrecord"
        assert orig_data is not None, "Balgruuf should have DATA subrecord"


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Balgruuf should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Balgruuf not in saved plugin"

            # Subrecord order must match xEdit expectations.
            # RNAM must come before AIDT/DNAM/PNAM, not after.
            sigs = [sr.signature for sr in patched.subrecords]
            _assert_subrecord_order(sigs, [
                'EDID', 'ACBS', 'SNAM', 'RNAM', 'AIDT', 'FULL',
                'DNAM', 'PNAM', 'TINI',
            ])

            # RNAM must resolve to a real race in a valid master
            _assert_valid_formid(reloaded, 'RNAM', patched, all_plugins)

            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'NordRace', \
                f"NPC race not NordRace, got {race_edid}"

            assert patched.get_subrecord('ACBS') is not None, "ACBS missing"
            assert patched.get_subrecord('ACBS').data == orig_acbs, \
                "ACBS data changed"

            assert patched.get_subrecord('AIDT') is not None, "AIDT missing"
            assert patched.get_subrecord('AIDT').data == orig_aidt, \
                "AIDT data changed"

            assert patched.get_subrecord('DNAM') is not None, "DNAM missing"
            assert patched.get_subrecord('DNAM').data == orig_dnam, \
                "DNAM data changed"

            assert patched.get_subrecord('FULL') is not None, "FULL missing"
            assert patched.get_subrecord('FULL').get_string() == orig_full_name, \
                "FULL name changed"

            if orig_shrt is not None:
                assert patched.get_subrecord('SHRT') is not None, "SHRT missing"

            assert patched.get_subrecord('DATA') is not None, "DATA missing"
            assert patched.get_subrecord('DATA').data == orig_data, \
                "DATA (weight) changed"

            assert patched.get_subrecord('NAM9') is None, "NAM9 should be removed"
            assert patched.get_subrecord('FTST') is None, "FTST should be removed"

            # Should have tint layers and QNAM from skin tone
            tinis = patched.get_subrecords('TINI')
            assert len(tinis) > 0, "Should have tint layers"
            qnam = patched.get_subrecord('QNAM')
            assert qnam is not None, "Should have QNAM from skin tone"

            # All TIAS values must be valid TIRS from the race's presets
            import struct as st
            tint_srs = patched.subrecords
            for sr in tint_srs:
                if sr.signature == 'TIAS':
                    tias = st.unpack('<H', sr.data[:2])[0]
                    assert tias > 200, \
                        f"TIAS={tias} looks like an array index, " \
                        f"not a TIRS preset value"

            # Balgruuf should not have dirt — vanilla Balgruuf has no dirt
            from furrifier.models import Sex as SexEnum
            dirt_tinis = set()
            lykaios_key = ('YASLykaiosRace', SexEnum.MALE_ADULT)
            if lykaios_key in race_tints:
                for cname, assets in race_tints[lykaios_key].classes.items():
                    if cname == 'Dirt':
                        for asset in assets:
                            dirt_tinis.add(asset.index)

            for sr in patched.subrecords:
                if sr.signature == 'TINI':
                    tini = st.unpack('<H', sr.data[:2])[0]
                    assert tini not in dirt_tinis, \
                        f"Balgruuf has dirt tint TINI={tini} " \
                        f"but vanilla Balgruuf has no dirt"

            # All PNAM headpart FormIDs must resolve and be male
            pnams = patched.get_subrecords('PNAM')
            assert len(pnams) > 0, "Should have at least one headpart"
            for pnam in pnams:
                fid = pnam.get_form_id()
                assert fid.value != 0, "PNAM FormID is null"
                master_idx = fid.file_index
                masters = reloaded.header.masters
                assert master_idx < len(masters), \
                    f"PNAM master index {master_idx} out of range " \
                    f"({len(masters)} masters)"

                # Headpart must not be female-only
                obj_id = fid.object_index
                for hp in all_headparts.values():
                    if hp.record and (hp.record.form_id.value & 0x00FFFFFF) == obj_id:
                        data_sr = hp.record.get_subrecord('DATA')
                        if data_sr and data_sr.size >= 1:
                            flags = data_sr.data[0]
                            is_male = bool(flags & 0x02)
                            is_female = bool(flags & 0x04)
                            assert not (is_female and not is_male), \
                                f"Headpart {hp.editor_id} is female-only " \
                                f"on male NPC Balgruuf"
                        break

        furrify_and_check(write, verify)


    def test_angvid(self, furrify_and_check, all_plugins):
        """Angvid: furrifiable, no crash."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Angvid')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Angvid not in saved plugin"

        furrify_and_check(write, verify)


    def test_corpse_prisoner(self, furrify_and_check, all_plugins, plugin_set):
        """CorpsePrisoner: no negative tint indices after save.

        CorpsePrisonerNordMale inherits Traits (appearance) via a TPLT
        template chain. Furrify the whole chain for a visually
        consistent result in xEdit.
        """
        npc, _ = find_record(all_plugins, 'NPC_', 'CorpsePrisonerNordMale')
        assert npc is not None
        templates = _get_template_chain(npc, plugin_set)
        form_id = npc.form_id


        def write(furry_ctx):
            for t in reversed(templates):
                furry_ctx.furrify_npc(t)
            furry_ctx.furrify_npc(npc)


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            if patched is not None:
                for sr in patched.get_subrecords('TINI'):
                    val = struct.unpack('<H', sr.data)[0]
                    assert val < 65000, \
                        f"Tint index {val} looks negative/invalid"

        furrify_and_check(write, verify)



    def test_delphine_has_hair(self, furrify_and_check, all_plugins,
                               all_headparts):
        """Delphine: should have hair after furrification."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Delphine')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            assert _has_headpart_containing(patched, all_headparts, 'Hair'), \
                "Delphine should have hair"

        furrify_and_check(write, verify)



    def test_ingun_has_hair(self, furrify_and_check, all_plugins,
                            all_headparts):
        """Ingun: female NPC gets hair assigned."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Ingun')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            assert _has_headpart_containing(patched, all_headparts, 'Hair'), \
                "Ingun should have hair"

        furrify_and_check(write, verify)


    # -- Imperial --

    def test_rune_imperial_male(self, furrify_and_check, all_plugins,
                                 races_by_edid):
        """Rune: Imperial male furrifies to Kettu."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Rune')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Rune should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Rune not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'ImperialRace', \
                f"Rune race should stay ImperialRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_arcadia_imperial_female(self, furrify_and_check, all_plugins,
                                      races_by_edid):
        """Arcadia: Imperial female furrifies to Kettu."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Arcadia')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Arcadia should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Arcadia not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'ImperialRace', \
                f"Arcadia race should stay ImperialRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Breton --

    def test_belethor_breton_male(self, furrify_and_check, all_plugins,
                                   races_by_edid):
        """Belethor: Breton male furrifies to Kygarra."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Belethor')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Belethor should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Belethor not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'BretonRace', \
                f"Belethor race should stay BretonRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_muiri_breton_female(self, furrify_and_check, all_plugins,
                                  races_by_edid):
        """Muiri: Breton female furrifies to Kygarra."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Muiri')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Muiri should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Muiri not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'BretonRace', \
                f"Muiri race should stay BretonRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Redguard --

    def test_amren_redguard_male(self, furrify_and_check, all_plugins,
                                  races_by_edid):
        """Amren: Redguard male furrifies to Xeba."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Amren')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Amren should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Amren not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'RedguardRace', \
                f"Amren race should stay RedguardRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_saadia_redguard_female(self, furrify_and_check, all_plugins,
                                     races_by_edid):
        """Saadia: Redguard female furrifies to Xeba."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Saadia')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Saadia should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Saadia not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'RedguardRace', \
                f"Saadia race should stay RedguardRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- High Elf --

    def test_ancano_highelf_male(self, furrify_and_check, all_plugins,
                                  races_by_edid):
        """Ancano: High Elf male furrifies to Maha."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Ancano')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Ancano should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Ancano not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'HighElfRace', \
                f"Ancano race should stay HighElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_elenwen_highelf_female(self, furrify_and_check, all_plugins,
                                     races_by_edid):
        """Elenwen: High Elf female furrifies to Maha."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Elenwen')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Elenwen should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Elenwen not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'HighElfRace', \
                f"Elenwen race should stay HighElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Wood Elf --

    def test_faendal_woodelf_male(self, furrify_and_check, all_plugins,
                                   races_by_edid):
        """Faendal: Wood Elf male furrifies to Duma."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Faendal')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Faendal should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Faendal not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'WoodElfRace', \
                f"Faendal race should stay WoodElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_nivenor_woodelf_female(self, furrify_and_check, all_plugins,
                                     races_by_edid):
        """Nivenor: Wood Elf female furrifies to Duma."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Nivenor')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Nivenor should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Nivenor not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'WoodElfRace', \
                f"Nivenor race should stay WoodElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Dark Elf --

    def test_athis_darkelf_male(self, furrify_and_check, all_plugins,
                                 races_by_edid):
        """Athis: Dark Elf male furrifies to Kalo."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Athis')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Athis should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Athis not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'DarkElfRace', \
                f"Athis race should stay DarkElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_irileth_darkelf_female(self, furrify_and_check, all_plugins,
                                     races_by_edid):
        """Irileth: Dark Elf female furrifies to Kalo."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Irileth')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Irileth should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Irileth not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_edid)
            assert race_edid == 'DarkElfRace', \
                f"Irileth race should stay DarkElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Faction-based subraces --

    def test_forsworn_becomes_reachman(self, furrify_and_check, all_plugins,
                                       races_by_edid):
        """Forsworn Breton male becomes Reachman race."""
        npc, _ = find_record(all_plugins, 'NPC_',
                             'EncForsworn01Melee1HBretonM01')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            race_edid = _get_race_edid(patched, races_by_edid, reloaded)
            assert race_edid == 'YASReachmanRace', \
                f"Forsworn should be Reachman, got {race_edid}"

        furrify_and_check(write, verify)


    def test_ainethach_becomes_reachman(self, furrify_and_check, all_plugins,
                                        races_by_edid):
        """Ainethach becomes Reachman."""
        npc, _ = find_record(all_plugins, 'NPC_', 'Ainethach')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            race_edid = _get_race_edid(patched, races_by_edid, reloaded)
            assert race_edid == 'YASReachmanRace', \
                f"Ainethach should be Reachman, got {race_edid}"

        furrify_and_check(write, verify)


# ===================================================================
# Pure logic tests (no save/reload needed)
# ===================================================================


class TestNPCAliases:
    """NPC alias resolution works for deterministic assignment."""

    def test_astrid_alias(self):
        assert unalias('AstridEnd') == 'Astrid'


    def test_non_alias(self):
        assert unalias('AstridXXX') == 'AstridXXX'


    def test_cicero_aliases(self):
        assert unalias('CiceroDawnstar') == 'Cicero'
        assert unalias('CiceroRoad') == 'Cicero'


class TestDetermineNPCRace:
    """Race determination from real plugin data."""

    def test_nord_is_furrifiable(self, all_plugins, furry_ctx):
        npc, _ = find_record(all_plugins, 'NPC_', 'BalgruuftheGreater')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'NordRace'
        assert furry == 'YASLykaiosRace'


    def test_khajiit_not_furrifiable(self, all_plugins, furry_ctx):
        npc, _ = find_record(all_plugins, 'NPC_', 'Kharjo')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is None, "Khajiit should not be furrifiable"


    def test_madanach_forced_to_reachman(self, all_plugins, furry_ctx):
        npc, _ = find_record(all_plugins, 'NPC_', 'Madanach')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert assigned == 'YASReachmanRace'
        assert furry == 'YASKonoiRace'


    def test_dark_elf_maps_to_kalo(self, all_plugins, furry_ctx):
        npc, _ = find_record(all_plugins, 'NPC_', 'Athis')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'DarkElfRace'
        assert furry == 'YASKaloRace'


    def test_breton_maps_to_kygarra(self, all_plugins, furry_ctx):
        npc, _ = find_record(all_plugins, 'NPC_', 'EncBandit01MagicBretonM')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'BretonRace'
        assert furry == 'YASKygarraRace'


class TestNPCChildRace:
    """Child NPCs keep their child race."""

    def test_eirid_stays_child(self, all_plugins, furry_ctx):
        npc, _ = find_record(all_plugins, 'NPC_', 'Eirid')
        assert npc is not None, "Eirid not found"
        race_result = furry_ctx.determine_npc_race(npc)
        if race_result is None:
            pytest.skip("Eirid's race not in furrification context")
        orig, assigned, furry = race_result
        assert 'Child' in furry or 'Child' in assigned, \
            f"Eirid should stay a child race, got {furry}"


class TestNPCSex:
    """Sex determination from real NPC records."""

    def test_male_npc(self, all_plugins, furry_ctx, races_by_edid):
        npc, _ = find_record(all_plugins, 'NPC_', 'BalgruuftheGreater')
        assert npc is not None
        race = races_by_edid.get('NordRace')
        assert furry_ctx.determine_npc_sex(npc, race) == Sex.MALE_ADULT


    def test_female_npc(self, all_plugins, furry_ctx, races_by_edid):
        npc, _ = find_record(all_plugins, 'NPC_', 'Delphine')
        assert npc is not None
        race = races_by_edid.get('BretonRace')
        sex = furry_ctx.determine_npc_sex(npc, race)
        assert sex.is_female


# ===================================================================
# Verify phase: save, reload, run all deferred verify callbacks
# This MUST be the last test in this file.
# ===================================================================


def test_verify_saved_plugin(patch):
    """Save the patch, reopen it, run all deferred verify callbacks."""
    failures = run_verify_phase(patch)
    if failures:
        pytest.fail(
            f"{len(failures)} verify failure(s) after save/reload:\n"
            + "\n".join(failures)
        )
