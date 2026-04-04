"""Integration tests for NPC furrification.

Ported from BDFurrySkyrimTEST.pas TestNPCs procedure.
Each test loads a real NPC from Skyrim.esm, furrifies it, and verifies
the result after save/reload. All furrified NPCs accumulate in the shared
patch plugin (FurrifierTEST.esp) for inspection in xEdit.

Tests that modify records use furrify_and_check: the write callback runs
immediately, the verify callback is deferred until test_verify_saved_plugin
saves, reopens the file, and runs all verify callbacks.
"""

import pytest

from furrifier.models import HeadpartType, Sex
from furrifier.vanilla_setup import unalias

from conftest import (
    requires_gamefiles, find_by_formid, run_verify_phase,
)


pytestmark = requires_gamefiles


def _get_race_edid(record, races_by_obj, plugin=None):
    """Get the EditorID of the race assigned to a patched NPC record.

    Checks the plugin's own records first (for patch-created subraces),
    then falls back to races_by_obj (source plugins, keyed by object_index).
    """
    rnam = record.get_subrecord('RNAM')
    if rnam is None:
        return None
    race_fid = rnam.get_form_id()
    # Check patch-created records first (subraces with new FormIDs)
    if plugin is not None:
        for rec in plugin.records:
            if rec.signature == 'RACE' and rec.form_id.value == race_fid.value:
                return rec.editor_id
    race_rec = races_by_obj.get(race_fid.object_index)
    return race_rec.editor_id if race_rec else None


def _assert_valid_formid(plugin, subrecord_sig, record, plugin_set):
    """Assert a FormID subrecord resolves to a real record.

    Checks that:
    1. The subrecord exists and is non-zero
    2. The master index byte points to a valid master
    3. The referenced record actually exists in that master
    """
    sr = record.get_subrecord(subrecord_sig)
    assert sr is not None, f"{subrecord_sig} subrecord missing"

    fid = sr.get_form_id()
    assert fid.value != 0, f"{subrecord_sig} FormID is null (0x00000000)"

    masters = plugin.header.masters
    assert fid.file_index < len(masters), \
        f"{subrecord_sig} master index {fid.file_index} out of range " \
        f"(plugin has {len(masters)} masters)"

    # Verify the record exists in the referenced master
    master_name = masters[fid.file_index]
    source = None
    for mp in plugin_set:
        if mp.file_path and mp.file_path.name.lower() == master_name.lower():
            source = mp
            break

    if source is not None:
        found = any(
            r.form_id.object_index == fid.object_index
            for r in source.records
            if r.signature == 'RACE'
        )
        assert found, \
            f"{subrecord_sig} FormID {fid.value:#010x} not found in {master_name}"


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


def _has_headpart_type(record, all_headparts, hp_type):
    """Check if the NPC has a headpart of the given type."""
    for sr in record.get_subrecords('PNAM'):
        obj_id = sr.get_form_id().object_index
        for hp in all_headparts.values():
            if hp.record and hp.record.form_id.object_index == obj_id:
                if hp.hp_type == hp_type:
                    return True
                break
    return False


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

    def test_balgruuf(self, furrify_and_check, plugin_set, races_by_obj,
                      all_headparts, race_tints):
        """Balgruuf: race stays NordRace, base data preserved."""
        npc = plugin_set.get_record_by_edid('NPC_', 'BalgruuftheGreater')
        assert npc is not None

        orig_acbs = npc.get_subrecord('ACBS').data[:]
        orig_aidt = npc.get_subrecord('AIDT').data[:]
        orig_dnam = npc.get_subrecord('DNAM').data[:]
        form_id = npc.form_id


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
            _assert_valid_formid(reloaded, 'RNAM', patched, plugin_set)

            race_edid = _get_race_edid(patched, races_by_obj)
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

            full = patched.get_subrecord('FULL')
            assert full is not None, "FULL missing"
            assert full.get_string() == 'Balgruuf the Greater', \
                f"FULL should be 'Balgruuf the Greater', got {full.get_string()!r}"

            shrt = patched.get_subrecord('SHRT')
            assert shrt is not None, "SHRT missing"
            assert shrt.get_string() == 'Balgruuf', \
                f"SHRT should be 'Balgruuf', got {shrt.get_string()!r}"

            # Should have tint layers and QNAM from skin tone
            tinis = patched.get_subrecords('TINI')
            assert len(tinis) > 0, "Should have tint layers"
            qnam = patched.get_subrecord('QNAM')
            assert qnam is not None, "Should have QNAM from skin tone"

            # All TIAS values must be valid TIRS from the race's presets
            for sr in patched.get_subrecords('TIAS'):
                tias = sr.get_uint16()
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

            for sr in patched.get_subrecords('TINI'):
                tini = sr.get_uint16()
                assert tini not in dirt_tinis, \
                    f"Balgruuf has dirt tint TINI={tini} " \
                    f"but vanilla Balgruuf has no dirt"

            # All PNAM headpart FormIDs must resolve and be male
            pnams = patched.get_subrecords('PNAM')
            assert len(pnams) > 0, "Should have at least one headpart"
            for pnam in pnams:
                fid = pnam.get_form_id()
                assert fid.value != 0, "PNAM FormID is null"
                masters = reloaded.header.masters
                assert fid.file_index < len(masters), \
                    f"PNAM master index {fid.file_index} out of range " \
                    f"({len(masters)} masters)"

                # Headpart must not be female-only
                obj_id = fid.object_index
                for hp in all_headparts.values():
                    if hp.record and hp.record.form_id.object_index == obj_id:
                        flags = hp.record['DATA']
                        if flags is not None:
                            assert not (flags.Female and not flags.Male), \
                                f"Headpart {hp.editor_id} is female-only " \
                                f"on male NPC Balgruuf"
                        break

        furrify_and_check(write, verify)


    def test_angvid(self, furrify_and_check, plugin_set):
        """Angvid: furrifiable, no crash."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Angvid')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Angvid not in saved plugin"

        furrify_and_check(write, verify)


    def test_corpse_prisoner(self, furrify_and_check, plugin_set):
        """CorpsePrisoner: no negative tint indices after save.

        CorpsePrisonerNordMale inherits Traits (appearance) via a TPLT
        template chain. Furrify the whole chain for a visually
        consistent result in CK.
        """
        npc = plugin_set.get_record_by_edid('NPC_', 'CorpsePrisonerNordMale')
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
                    val = sr.get_uint16()
                    assert val < 65000, \
                        f"Tint index {val} looks negative/invalid"

        furrify_and_check(write, verify)


    def test_delphine_has_hair(self, furrify_and_check, plugin_set,
                               all_headparts):
        """Delphine: should have hair after furrification."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Delphine')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            assert _has_headpart_type(patched, all_headparts, HeadpartType.HAIR), \
                "Delphine should have hair"

        furrify_and_check(write, verify)


    def test_ingun_has_hair(self, furrify_and_check, plugin_set,
                            all_headparts):
        """Ingun: female NPC gets hair assigned."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Ingun')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            assert _has_headpart_type(patched, all_headparts, HeadpartType.HAIR), \
                "Ingun should have hair"

        furrify_and_check(write, verify)


    # -- Imperial --

    def test_rune_imperial_male(self, furrify_and_check, plugin_set,
                                 races_by_obj):
        """Rune: Imperial male furrifies to Kettu."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Rune')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Rune should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Rune not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'ImperialRace', \
                f"Rune race should stay ImperialRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_arcadia_imperial_female(self, furrify_and_check, plugin_set,
                                      races_by_obj):
        """Arcadia: Imperial female furrifies to Kettu."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Arcadia')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Arcadia should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Arcadia not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'ImperialRace', \
                f"Arcadia race should stay ImperialRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Breton --

    def test_belethor_breton_male(self, furrify_and_check, plugin_set,
                                   races_by_obj):
        """Belethor: Breton male furrifies to Kygarra."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Belethor')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Belethor should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Belethor not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'BretonRace', \
                f"Belethor race should stay BretonRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_muiri_breton_female(self, furrify_and_check, plugin_set,
                                  races_by_obj):
        """Muiri: Breton female furrifies to Kygarra."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Muiri')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Muiri should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Muiri not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'BretonRace', \
                f"Muiri race should stay BretonRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Redguard --

    def test_amren_redguard_male(self, furrify_and_check, plugin_set,
                                  races_by_obj):
        """Amren: Redguard male furrifies to Xeba."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Amren')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Amren should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Amren not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'RedguardRace', \
                f"Amren race should stay RedguardRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_saadia_redguard_female(self, furrify_and_check, plugin_set,
                                     races_by_obj):
        """Saadia: Redguard female furrifies to Xeba."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Saadia')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Saadia should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Saadia not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'RedguardRace', \
                f"Saadia race should stay RedguardRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- High Elf --

    def test_ancano_highelf_male(self, furrify_and_check, plugin_set,
                                  races_by_obj):
        """Ancano: High Elf male furrifies to Maha."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Ancano')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Ancano should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Ancano not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'HighElfRace', \
                f"Ancano race should stay HighElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_elenwen_highelf_female(self, furrify_and_check, plugin_set,
                                     races_by_obj):
        """Elenwen: High Elf female furrifies to Maha."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Elenwen')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Elenwen should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Elenwen not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'HighElfRace', \
                f"Elenwen race should stay HighElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Wood Elf --

    def test_faendal_woodelf_male(self, furrify_and_check, plugin_set,
                                   races_by_obj):
        """Faendal: Wood Elf male furrifies to Duma."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Faendal')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Faendal should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Faendal not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'WoodElfRace', \
                f"Faendal race should stay WoodElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_nivenor_woodelf_female(self, furrify_and_check, plugin_set,
                                     races_by_obj):
        """Nivenor: Wood Elf female furrifies to Duma."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Nivenor')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Nivenor should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Nivenor not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'WoodElfRace', \
                f"Nivenor race should stay WoodElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Dark Elf --

    def test_athis_darkelf_male(self, furrify_and_check, plugin_set,
                                 races_by_obj):
        """Athis: Dark Elf male furrifies to Kalo."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Athis')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Athis should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Athis not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'DarkElfRace', \
                f"Athis race should stay DarkElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)


    def test_irileth_darkelf_female(self, furrify_and_check, plugin_set,
                                     races_by_obj):
        """Irileth: Dark Elf female furrifies to Kalo."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Irileth')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None, "Irileth should be furrifiable"


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None, "Irileth not in saved plugin"
            race_edid = _get_race_edid(patched, races_by_obj)
            assert race_edid == 'DarkElfRace', \
                f"Irileth race should stay DarkElfRace, got {race_edid}"
            assert len(patched.get_subrecords('PNAM')) > 0, "Should have headparts"
            assert _tint_layer_count(patched) > 0, "Should have tint layers"

        furrify_and_check(write, verify)

    # -- Faction-based subraces --

    def test_forsworn_becomes_reachman(self, furrify_and_check, plugin_set,
                                       races_by_obj):
        """Forsworn Breton male becomes Reachman race."""
        npc = plugin_set.get_record_by_edid(
            'NPC_', 'EncForsworn01Melee1HBretonM01')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            race_edid = _get_race_edid(patched, races_by_obj, reloaded)
            assert race_edid == 'YASReachmanRace', \
                f"Forsworn should be Reachman, got {race_edid}"

        furrify_and_check(write, verify)


    def test_ainethach_becomes_reachman(self, furrify_and_check, plugin_set,
                                        races_by_obj):
        """Ainethach becomes Reachman."""
        npc = plugin_set.get_record_by_edid('NPC_', 'Ainethach')
        assert npc is not None
        form_id = npc.form_id


        def write(furry_ctx):
            result = furry_ctx.furrify_npc(npc)
            assert result is not None


        def verify(reloaded):
            patched = find_by_formid(reloaded, form_id)
            assert patched is not None
            race_edid = _get_race_edid(patched, races_by_obj, reloaded)
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

    def test_nord_is_furrifiable(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'BalgruuftheGreater')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'NordRace'
        assert furry == 'YASLykaiosRace'


    def test_khajiit_not_furrifiable(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'Kharjo')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is None, "Khajiit should not be furrifiable"


    def test_madanach_forced_to_reachman(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'Madanach')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert assigned == 'YASReachmanRace'
        assert furry == 'YASKonoiRace'


    def test_dark_elf_maps_to_kalo(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'Athis')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'DarkElfRace'
        assert furry == 'YASKaloRace'


    def test_breton_maps_to_kygarra(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'EncBandit01MagicBretonM')
        assert npc is not None
        result = furry_ctx.determine_npc_race(npc)
        assert result is not None
        orig, assigned, furry = result
        assert orig == 'BretonRace'
        assert furry == 'YASKygarraRace'


class TestNPCChildRace:
    """Child NPCs keep their child race."""

    def test_eirid_stays_child(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'Eirid')
        assert npc is not None, "Eirid not found"
        race_result = furry_ctx.determine_npc_race(npc)
        if race_result is None:
            pytest.skip("Eirid's race not in furrification context")
        orig, assigned, furry = race_result
        assert 'Child' in furry or 'Child' in assigned, \
            f"Eirid should stay a child race, got {furry}"


class TestNPCSex:
    """Sex determination from real NPC records."""

    def test_male_npc(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'BalgruuftheGreater')
        assert npc is not None
        race = plugin_set.get_record_by_edid('RACE', 'NordRace')
        assert furry_ctx.determine_npc_sex(npc, race) == Sex.MALE_ADULT


    def test_female_npc(self, plugin_set, furry_ctx):
        npc = plugin_set.get_record_by_edid('NPC_', 'Delphine')
        assert npc is not None
        race = plugin_set.get_record_by_edid('RACE', 'BretonRace')
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
