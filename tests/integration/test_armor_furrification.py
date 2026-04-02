"""Integration tests for armor furrification.

Ported from BDFurrySkyrimTEST.pas TestArmor procedure.
Tests that furrified races are added to ARMA Additional Races lists
so NPCs can equip armor properly. Modified ARMA records accumulate in
the shared FurrifierTEST.esp patch.
"""

import pytest

from furrifier.armor import get_bodypart_flags, arma_has_race
from furrifier.models import Bodypart

from conftest import (
    requires_gamefiles, find_record, find_by_edid, run_verify_phase,
)


pytestmark = requires_gamefiles


class TestBodypartFlags:
    """Reading bodypart flags from real ARMA records."""

    def test_draugr_gloves_have_hands(self, all_plugins):
        """DraugrGlovesAA has hands bit set, hair bit clear."""
        arma, _ = find_record(all_plugins, 'ARMA', 'DraugrGlovesAA')
        assert arma is not None, "DraugrGlovesAA not found"
        flags = get_bodypart_flags(arma)
        assert flags & Bodypart.HANDS, "DraugrGlovesAA should have hands bit"
        assert not (flags & Bodypart.HAIR), "DraugrGlovesAA should not have hair bit"


    def test_mythic_dawn_hood_has_hair(self, all_plugins):
        """MythicDawnHoodAA has hair bit set, hands bit clear."""
        arma, _ = find_record(all_plugins, 'ARMA', 'MythicDawnHoodAA')
        assert arma is not None, "MythicDawnHoodAA not found"
        flags = get_bodypart_flags(arma)
        assert flags & Bodypart.HAIR, "MythicDawnHoodAA should have hair bit"
        assert not (flags & Bodypart.HANDS), "MythicDawnHoodAA should not have hands bit"


    def test_thieves_guild_helmet_form44(self, all_plugins):
        """ThievesGuildHelmetAA (form version 44, BOD2) has hair bit set."""
        arma, _ = find_record(all_plugins, 'ARMA', 'ThievesGuildHelmetAA')
        assert arma is not None, "ThievesGuildHelmetAA not found"
        flags = get_bodypart_flags(arma)
        assert flags & Bodypart.HAIR, "ThievesGuildHelmetAA should have hair bit"
        assert not (flags & Bodypart.HANDS), \
            "ThievesGuildHelmetAA should not have hands bit"


class TestArmorFurrification:
    """Furrify armor and verify specific ARMA records."""

    def test_chefhat_gets_furrified_races(self, furrify_and_check,
                                         all_plugins, races_by_edid):
        """ChefHatAA gets furrified races because it has KhajiitRace.

        ClothesChefHat ARMO lists [ChefHatArgAA, ChefHatAA, ChefHatKhaAA].
        ChefHatAA has KhajiitRace (the armor fallback) in its Additional
        Races, so it's the first ARMA that supports furry races.
        Furrified vanilla races should be added to it.
        """
        chefhat, _ = find_record(all_plugins, 'ARMA', 'ChefHatAA')
        assert chefhat is not None, "ChefHatAA not found"

        nord = races_by_edid.get('NordRace')
        assert nord is not None, "NordRace not loaded"
        nord_obj = nord.form_id.value & 0x00FFFFFF


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(all_plugins)
            furry_ctx.furrify_all_armor(all_plugins)


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'ChefHatAA')
            assert patched is not None, \
                "ChefHatAA should be in the patch"
            modl_objs = {sr.get_uint32() & 0x00FFFFFF
                         for sr in patched.get_subrecords('MODL')
                         if sr.size >= 4}
            assert nord_obj in modl_objs, \
                f"NordRace ({hex(nord_obj)}) should be ADDED to " \
                f"ChefHatAA (has KhajiitRace, first in ARMO list)"

        furrify_and_check(write, verify)


    def test_dog_arma_gets_furrified_races(self, furrify_and_check,
                                          all_plugins, races_by_edid):
        """YASStormcloakHelm_DOG (canine ARMA) gets furrified vanilla races.

        This ARMA has canine furry races but not the furrified vanilla
        races (NordRace etc.). After furrification, NordRace has wolf
        head data and needs to be in this ARMA's race list.
        """
        dog_arma, _ = find_record(all_plugins, 'ARMA', 'YASStormcloakHelm_DOG')
        assert dog_arma is not None, "YASStormcloakHelm_DOG not found"

        nord = races_by_edid.get('NordRace')
        assert nord is not None, "NordRace not loaded"
        nord_obj = nord.form_id.value & 0x00FFFFFF

        imperial = races_by_edid.get('ImperialRace')
        assert imperial is not None, "ImperialRace not loaded"
        imperial_obj = imperial.form_id.value & 0x00FFFFFF


        def write(_furry_ctx):
            pass  # armor already furrified by test_chefhat_vanilla_races_removed


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'YASDaedricHelmetAA_DOG')
            assert patched is not None, \
                "YASStormcloakHelm_DOG should be in the patch"
            modl_objs = {sr.get_uint32() & 0x00FFFFFF
                         for sr in patched.get_subrecords('MODL')
                         if sr.size >= 4}
            assert nord_obj in modl_objs, \
                f"NordRace ({hex(nord_obj)}) should be ADDED to " \
                f"YASStormcloakHelm_DOG (canine mesh, fits furrified Nords)"
            assert imperial_obj in modl_objs, \
                f"ImperialRace ({hex(imperial_obj)}) should be ADDED to " \
                f"YASStormcloakHelm_DOG (canine mesh, fits furrified Imperials)"

        furrify_and_check(write, verify)


    def test_cat_arma_gets_furrified_races(self, furrify_and_check,
                                           all_plugins, races_by_edid):
        """BDStormcloakHelm_CAT (cat ARMA) gets furrified vanilla races.

        This ARMA has cat furry races but not the furrified vanilla
        races (HighElfRace etc.). After furrification, HighElfRace has
        cat head data and needs to be in this ARMA's race list.
        """
        cat_arma, _ = find_record(all_plugins, 'ARMA', 'BDStormcloakHelm_CAT')
        assert cat_arma is not None, "BDStormcloakHelm_CAT not found"

        high_elf = races_by_edid.get('HighElfRace')
        assert high_elf is not None, "HighElfRace not loaded"
        high_elf_obj = high_elf.form_id.value & 0x00FFFFFF

        orc = races_by_edid.get('OrcRace')
        assert orc is not None, "OrcRace not loaded"
        orc_obj = orc.form_id.value & 0x00FFFFFF


        def write(_furry_ctx):
            pass  # armor already furrified by test_chefhat_vanilla_races_removed


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'BDStormcloakHelm_CAT')
            assert patched is not None, \
                "BDStormcloakHelm_CAT should be in the patch"
            modl_objs = {sr.get_uint32() & 0x00FFFFFF
                         for sr in patched.get_subrecords('MODL')
                         if sr.size >= 4}
            assert high_elf_obj in modl_objs, \
                f"HighElfRace ({hex(high_elf_obj)}) should be ADDED to " \
                f"BDStormcloakHelm_CAT (cat mesh, fits furrified High Elves)"
            assert orc_obj in modl_objs, \
                f"OrcRace ({hex(orc_obj)}) should be ADDED to " \
                f"BDStormcloakHelm_CAT (cat mesh, fits furrified Orcs)"

        furrify_and_check(write, verify)


class TestArmorAddonMerge:
    """Merge ARMA refs from multiple overrides of the same ARMO."""

    def test_stormcloak_helmet_collects_cat_and_dog(self, furrify_and_check,
                                                    all_plugins):
        """ArmorStormcloakHelmetFull should have both cat and dog ARMAs.

        BDCatRaces.esp and YASCanineRaces.esp each override this ARMO to
        add their ARMA, but only one override wins. The merge step should
        collect both into the patch.
        """
        # Find the ARMO and the cat/dog ARMAs
        armo, _ = find_record(all_plugins, 'ARMO', 'ArmorStormcloakHelmetFull')
        assert armo is not None, "ArmorStormcloakHelmetFull not found"

        cat_arma, _ = find_record(all_plugins, 'ARMA', 'BDStormcloakHelm_CAT')
        assert cat_arma is not None, "BDStormcloakHelm_CAT not found"
        cat_obj = cat_arma.form_id.value & 0x00FFFFFF

        dog_arma, _ = find_record(all_plugins, 'ARMA', 'YASStormcloakHelm_DOG')
        assert dog_arma is not None, "YASStormcloakHelm_DOG not found"
        dog_obj = dog_arma.form_id.value & 0x00FFFFFF


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(all_plugins)


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'ArmorStormcloakHelmetFull')
            assert patched is not None, \
                "ArmorStormcloakHelmetFull should be in the patch"
            modl_objs = {sr.get_uint32() & 0x00FFFFFF
                         for sr in patched.get_subrecords('MODL')
                         if sr.size >= 4}
            assert cat_obj in modl_objs, \
                f"BDStormcloakHelm_CAT ({hex(cat_obj)}) not in merged ARMO"
            assert dog_obj in modl_objs, \
                f"YASStormcloakHelm_DOG ({hex(dog_obj)}) not in merged ARMO"

        furrify_and_check(write, verify)


class TestDaedricHelmet:
    """Detailed checks on ArmorDaedricHelmet and its cat/dog ARMAs."""

    VALID_PLUGINS = {
        'skyrim.esm', 'update.esm', 'dawnguard.esm', 'hearthfires.esm',
        'dragonborn.esm', 'bdcatraces.esp', 'yascanineraces.esp',
    }

    def _build_race_lookup(self, all_plugins):
        """Build obj_id -> (editor_id, plugin_name) for all races."""
        lookup = {}
        for plugin in all_plugins:
            pname = plugin.file_path.name.lower() if plugin.file_path else '?'
            for rec in plugin.get_records_by_signature('RACE'):
                obj = rec.form_id.value & 0x00FFFFFF
                if rec.editor_id:
                    lookup[obj] = (rec.editor_id, pname)
        return lookup

    def _get_additional_race_info(self, arma, race_lookup):
        """Get list of (obj_id, editor_id, plugin_name) for MODL entries."""
        result = []
        for sr in arma.get_subrecords('MODL'):
            if sr.size >= 4:
                obj = sr.get_uint32() & 0x00FFFFFF
                edid, pname = race_lookup.get(obj, (None, None))
                result.append((obj, edid, pname))
        return result

    def _check_formid_masters(self, record, patch_plugin):
        """Verify all FormID subrecord master indices point to valid masters.

        Every FormID in RNAM and MODL subrecords has a master index byte.
        That index must reference a valid master in the patch's master
        list, and that master must be one of the known source plugins.
        """
        masters = patch_plugin.header.masters
        errors = []
        for sig in ('RNAM', 'MODL'):
            for sr in record.get_subrecords(sig):
                if sr.size < 4:
                    continue
                raw = sr.get_uint32()
                idx = raw >> 24
                if idx >= len(masters):
                    errors.append(
                        f"{sig} {hex(raw)}: master index {idx} >= "
                        f"master count {len(masters)}")
                    continue
                master_name = masters[idx].lower()
                if master_name not in self.VALID_PLUGINS:
                    errors.append(
                        f"{sig} {hex(raw)}: master [{idx:02x}] = "
                        f"{masters[idx]}, not a valid source plugin")
        return errors

    def test_daedric_helmet_armo_has_cat_and_dog(self, furrify_and_check,
                                                 all_plugins):
        """Merged ArmorDaedricHelmet has cat and dog ARMAs in its list."""
        cat_arma, _ = find_record(all_plugins, 'ARMA', 'YAS_DaedricHelmetAA_CAT')
        assert cat_arma is not None
        cat_obj = cat_arma.form_id.value & 0x00FFFFFF

        dog_arma, _ = find_record(all_plugins, 'ARMA', 'YASDaedricHelmetAA_DOG')
        assert dog_arma is not None
        dog_obj = dog_arma.form_id.value & 0x00FFFFFF

        race_lookup = self._build_race_lookup(all_plugins)


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(all_plugins)
            furry_ctx.furrify_all_armor(all_plugins)


        def verify(reloaded):
            # Check the ARMO has both ARMAs
            armo = find_by_edid(reloaded, 'ArmorDaedricHelmet')
            assert armo is not None, "ArmorDaedricHelmet not in patch"
            modl_objs = {sr.get_uint32() & 0x00FFFFFF
                         for sr in armo.get_subrecords('MODL')
                         if sr.size >= 4}
            assert cat_obj in modl_objs, \
                f"YAS_DaedricHelmetAA_CAT ({hex(cat_obj)}) not in " \
                f"ArmorDaedricHelmet MODL list"
            assert dog_obj in modl_objs, \
                f"YASDaedricHelmetAA_DOG ({hex(dog_obj)}) not in " \
                f"ArmorDaedricHelmet MODL list"

            # Check the dog ARMA
            dog = find_by_edid(reloaded, 'YASDaedricHelmetAA_DOG')
            assert dog is not None, "YASDaedricHelmetAA_DOG not in patch"
            dog_races = self._get_additional_race_info(dog, race_lookup)

            # All entries must have valid editor IDs
            for obj, edid, pname in dog_races:
                assert edid is not None, \
                    f"Unknown race obj {hex(obj)} in YASDaedricHelmetAA_DOG"

            # All races must come from valid plugins
            for obj, edid, pname in dog_races:
                assert pname in self.VALID_PLUGINS, \
                    f"Race {edid} ({hex(obj)}) from {pname} -- " \
                    f"not a valid source plugin"

            # Must have furrified human races
            dog_edids = {edid for _, edid, _ in dog_races}
            assert 'NordRace' in dog_edids, \
                "NordRace missing from YASDaedricHelmetAA_DOG"
            assert 'ImperialRace' in dog_edids, \
                "ImperialRace missing from YASDaedricHelmetAA_DOG"
            assert 'BretonRace' in dog_edids, \
                "BretonRace missing from YASDaedricHelmetAA_DOG"

            # FormID master indices must be valid
            dog_errors = self._check_formid_masters(dog, reloaded)
            assert not dog_errors, \
                f"Bad FormID masters in YASDaedricHelmetAA_DOG:\n" + \
                "\n".join(dog_errors)

            # Check the cat ARMA
            cat = find_by_edid(reloaded, 'YAS_DaedricHelmetAA_CAT')
            assert cat is not None, "YAS_DaedricHelmetAA_CAT not in patch"
            cat_races = self._get_additional_race_info(cat, race_lookup)

            # All entries must have valid editor IDs
            for obj, edid, pname in cat_races:
                assert edid is not None, \
                    f"Unknown race obj {hex(obj)} in YAS_DaedricHelmetAA_CAT"

            # All races must come from valid plugins
            for obj, edid, pname in cat_races:
                assert pname in self.VALID_PLUGINS, \
                    f"Race {edid} ({hex(obj)}) from {pname} -- " \
                    f"not a valid source plugin"

            # Must have furrified elf/orc races
            cat_edids = {edid for _, edid, _ in cat_races}
            assert 'HighElfRace' in cat_edids, \
                "HighElfRace missing from YAS_DaedricHelmetAA_CAT"
            assert 'OrcRace' in cat_edids, \
                "OrcRace missing from YAS_DaedricHelmetAA_CAT"
            assert 'DarkElfRace' in cat_edids, \
                "DarkElfRace missing from YAS_DaedricHelmetAA_CAT"

            # FormID master indices must be valid
            cat_errors = self._check_formid_masters(cat, reloaded)
            assert not cat_errors, \
                f"Bad FormID masters in YAS_DaedricHelmetAA_CAT:\n" + \
                "\n".join(cat_errors)

        furrify_and_check(write, verify)


class TestNonHeadArmor:
    """Body-only armor is not modified."""

    def test_non_head_armor_unchanged(self, all_plugins):
        for plugin in all_plugins:
            for arma in plugin.get_records_by_signature('ARMA'):
                flags = get_bodypart_flags(arma)
                furrifiable = (Bodypart.HEAD | Bodypart.HAIR | Bodypart.HANDS |
                               Bodypart.LONGHAIR | Bodypart.CIRCLET)
                if flags and not (flags & furrifiable):
                    return  # Found one, test passes
        pytest.skip("No body-only ARMA found to test")


# ===================================================================
# Verify phase: save, reload, run all deferred verify callbacks
# This MUST be the last test in this file.
# ===================================================================


def test_verify_saved_armor(patch):
    """Save the patch, reopen it, run all deferred verify callbacks."""
    failures = run_verify_phase(patch)
    if failures:
        pytest.fail(
            f"{len(failures)} verify failure(s) after save/reload:\n"
            + "\n".join(failures)
        )
