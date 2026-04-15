"""Integration tests for armor furrification.

Ported from BDFurrySkyrimTEST.pas TestArmor procedure.
Tests that furrified races are added to ARMA Additional Races lists
so NPCs can equip armor properly. Modified ARMA records accumulate in
the shared FurrifierTEST.esp patch.
"""

import pytest

from conftest import (
    requires_gamefiles, find_by_edid, run_verify_phase,
)


pytestmark = requires_gamefiles


def _get_bodypart_flags(arma):
    """Get bodypart flags from BOD2 or BODT."""
    bod = arma['BOD2'] or arma['BODT']
    assert bod is not None, f"{arma.editor_id} has no BOD2 or BODT"
    return bod['first_person_flags']


class TestBodypartFlags:
    """Reading bodypart flags from real ARMA records."""

    def test_draugr_gloves_have_hands(self, plugin_set):
        """DraugrGlovesAA has hands bit set, hair bit clear."""
        arma = plugin_set.get_record_by_edid('ARMA', 'DraugrGlovesAA')
        assert arma is not None, "DraugrGlovesAA not found"
        flags = _get_bodypart_flags(arma)
        assert flags.Hands, "DraugrGlovesAA should have hands bit"
        assert not flags.Hair, "DraugrGlovesAA should not have hair bit"


    def test_mythic_dawn_hood_has_hair(self, plugin_set):
        """MythicDawnHoodAA has hair bit set, hands bit clear."""
        arma = plugin_set.get_record_by_edid('ARMA', 'MythicDawnHoodAA')
        assert arma is not None, "MythicDawnHoodAA not found"
        flags = _get_bodypart_flags(arma)
        assert flags.Hair, "MythicDawnHoodAA should have hair bit"
        assert not flags.Hands, "MythicDawnHoodAA should not have hands bit"


    def test_thieves_guild_helmet_form44(self, plugin_set):
        """ThievesGuildHelmetAA (form version 44) has hair bit set."""
        arma = plugin_set.get_record_by_edid('ARMA', 'ThievesGuildHelmetAA')
        assert arma is not None, "ThievesGuildHelmetAA not found"
        flags = _get_bodypart_flags(arma)
        assert flags.Hair, "ThievesGuildHelmetAA should have hair bit"
        assert not flags.Hands, "ThievesGuildHelmetAA should not have hands bit"


class TestArmorFurrification:
    """Furrify armor and verify specific ARMA records."""

    def test_chefhat_gets_furrified_races(self, furrify_and_check,
                                         plugin_set):
        """ChefHatKhaAA claims furrified races via KhajiitRace fallback.

        ClothesChefHat ARMO lists [ChefHatArgAA, ChefHatAA, ChefHatKhaAA].
        ChefHatKhaAA has KhajiitRace (the armor fallback for furry races)
        so it claims furrified vanilla races. ChefHatAA should have them
        removed since it's not the claiming ARMA.
        """
        nord = plugin_set.get_record_by_edid('RACE', 'NordRace')
        assert nord is not None, "NordRace not loaded"
        nord_obj = nord.form_id.object_index


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(plugin_set)
            furry_ctx.furrify_all_armor(plugin_set)


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'ChefHatAA')
            assert patched is not None, \
                "ChefHatAA should be in the patch"
            modl_objs = {sr.get_form_id().object_index
                         for sr in patched.get_subrecords('MODL')
                         if sr.size >= 4}
            assert nord_obj not in modl_objs, \
                f"NordRace ({hex(nord_obj)}) should be REMOVED from " \
                f"ChefHatAA (ChefHatKhaAA claims it via KhajiitRace fallback)"

        furrify_and_check(write, verify)


    def test_dog_arma_gets_furrified_races(self, furrify_and_check,
                                          plugin_set):
        """YASStormcloakHelm_DOG (canine ARMA) gets furrified vanilla races.

        This ARMA has canine furry races but not the furrified vanilla
        races (NordRace etc.). After furrification, NordRace has wolf
        head data and needs to be in this ARMA's race list.
        """
        dog_arma = plugin_set.get_record_by_edid('ARMA', 'YASStormcloakHelm_DOG')
        assert dog_arma is not None, "YASStormcloakHelm_DOG not found"

        nord = plugin_set.get_record_by_edid('RACE', 'NordRace')
        assert nord is not None, "NordRace not loaded"
        nord_obj = nord.form_id.object_index

        imperial = plugin_set.get_record_by_edid('RACE', 'ImperialRace')
        assert imperial is not None, "ImperialRace not loaded"
        imperial_obj = imperial.form_id.object_index


        def write(_furry_ctx):
            pass  # armor already furrified by test_chefhat_vanilla_races_removed


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'YASDaedricHelmetAA_DOG')
            assert patched is not None, \
                "YASStormcloakHelm_DOG should be in the patch"
            modl_objs = {sr.get_form_id().object_index
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
                                           plugin_set):
        """BDStormcloakHelm_CAT (cat ARMA) gets furrified vanilla races.

        This ARMA has cat furry races but not the furrified vanilla
        races (HighElfRace etc.). After furrification, HighElfRace has
        cat head data and needs to be in this ARMA's race list.
        """
        cat_arma = plugin_set.get_record_by_edid('ARMA', 'BDStormcloakHelm_CAT')
        assert cat_arma is not None, "BDStormcloakHelm_CAT not found"

        high_elf = plugin_set.get_record_by_edid('RACE', 'HighElfRace')
        assert high_elf is not None, "HighElfRace not loaded"
        high_elf_obj = high_elf.form_id.object_index


        def write(_furry_ctx):
            pass  # armor already furrified by test_chefhat_vanilla_races_removed


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'BDStormcloakHelm_CAT')
            assert patched is not None, \
                "BDStormcloakHelm_CAT should be in the patch"
            modl_objs = {sr.get_form_id().object_index
                         for sr in patched.get_subrecords('MODL')
                         if sr.size >= 4}
            assert high_elf_obj in modl_objs, \
                f"HighElfRace ({hex(high_elf_obj)}) should be ADDED to " \
                f"BDStormcloakHelm_CAT (cat mesh, fits furrified High Elves)"

        furrify_and_check(write, verify)


class TestArmorAddonMerge:
    """Merge ARMA refs from multiple overrides of the same ARMO."""

    def test_stormcloak_helmet_collects_cat_and_dog(self, furrify_and_check,
                                                    plugin_set):
        """ArmorStormcloakHelmetFull should have both cat and dog ARMAs.

        BDCatRaces.esp and YASCanineRaces.esp each override this ARMO to
        add their ARMA, but only one override wins. The merge step should
        collect both into the patch.
        """
        # Find the ARMO and the cat/dog ARMAs
        armo = plugin_set.get_record_by_edid('ARMO', 'ArmorStormcloakHelmetFull')
        assert armo is not None, "ArmorStormcloakHelmetFull not found"

        cat_arma = plugin_set.get_record_by_edid('ARMA', 'BDStormcloakHelm_CAT')
        assert cat_arma is not None, "BDStormcloakHelm_CAT not found"
        cat_obj = cat_arma.form_id.object_index

        dog_arma = plugin_set.get_record_by_edid('ARMA', 'YASStormcloakHelm_DOG')
        assert dog_arma is not None, "YASStormcloakHelm_DOG not found"
        dog_obj = dog_arma.form_id.object_index


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(plugin_set)


        def verify(reloaded):
            patched = find_by_edid(reloaded, 'ArmorStormcloakHelmetFull')
            assert patched is not None, \
                "ArmorStormcloakHelmetFull should be in the patch"
            modl_objs = {sr.get_form_id().object_index
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

    def _build_race_lookup(self, plugin_set):
        """Build obj_id -> (editor_id, plugin_name) for all races."""
        lookup = {}
        for plugin in plugin_set:
            pname = plugin.file_path.name.lower() if plugin.file_path else '?'
            for rec in plugin.get_records_by_signature('RACE'):
                obj = rec.form_id.object_index
                if rec.editor_id:
                    lookup[obj] = (rec.editor_id, pname)
        return lookup

    def _get_additional_race_info(self, arma, race_lookup):
        """Get list of (obj_id, editor_id, plugin_name) for MODL entries."""
        result = []
        for sr in arma.get_subrecords('MODL'):
            if sr.size >= 4:
                obj = sr.get_form_id().object_index
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
                fid = sr.get_form_id()
                if fid.file_index >= len(masters):
                    errors.append(
                        f"{sig} {hex(fid.value)}: master index "
                        f"{fid.file_index} >= "
                        f"master count {len(masters)}")
                    continue
                master_name = masters[fid.file_index].lower()
                if master_name not in self.VALID_PLUGINS:
                    errors.append(
                        f"{sig} {hex(fid.value)}: master "
                        f"[{fid.file_index:02x}] = "
                        f"{masters[fid.file_index]}, "
                        f"not a valid source plugin")
        return errors

    def test_daedric_helmet_armo_has_cat_and_dog(self, furrify_and_check,
                                                 plugin_set):
        """Merged ArmorDaedricHelmet has cat and dog ARMAs in its list."""
        cat_arma = plugin_set.get_record_by_edid('ARMA', 'YAS_DaedricHelmetAA_CAT')
        assert cat_arma is not None
        cat_obj = cat_arma.form_id.object_index

        dog_arma = plugin_set.get_record_by_edid('ARMA', 'YASDaedricHelmetAA_DOG')
        assert dog_arma is not None
        dog_obj = dog_arma.form_id.object_index

        race_lookup = self._build_race_lookup(plugin_set)


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(plugin_set)
            furry_ctx.furrify_all_armor(plugin_set)


        def verify(reloaded):
            # Check the ARMO has both ARMAs
            armo = find_by_edid(reloaded, 'ArmorDaedricHelmet')
            assert armo is not None, "ArmorDaedricHelmet not in patch"
            modl_objs = {sr.get_form_id().object_index
                         for sr in armo.get_subrecords('MODL')
                         if sr.size >= 4}
            assert cat_obj in modl_objs, \
                f"YAS_DaedricHelmetAA_CAT ({hex(cat_obj)}) not in " \
                f"ArmorDaedricHelmet MODL list"
            assert dog_obj in modl_objs, \
                f"YASDaedricHelmetAA_DOG ({hex(dog_obj)}) not in " \
                f"ArmorDaedricHelmet MODL list"

            # Vanilla ARMA should have furrified races removed
            vanilla = find_by_edid(reloaded, 'DaedricHelmetAA')
            assert vanilla is not None, "DaedricHelmetAA not in patch"
            vanilla_races = self._get_additional_race_info(
                vanilla, race_lookup)
            vanilla_edids = {edid for _, edid, _ in vanilla_races}
            assert 'NordRace' not in vanilla_edids, \
                "NordRace should be REMOVED from DaedricHelmetAA " \
                "(has furry dog variant)"
            assert 'HighElfRace' not in vanilla_edids, \
                "HighElfRace should be REMOVED from DaedricHelmetAA " \
                "(has furry cat variant)"

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

            # Must have furrified elf races
            cat_edids = {edid for _, edid, _ in cat_races}
            assert 'HighElfRace' in cat_edids, \
                "HighElfRace missing from YAS_DaedricHelmetAA_CAT"
            assert 'DarkElfRace' in cat_edids, \
                "DarkElfRace missing from YAS_DaedricHelmetAA_CAT"

            # FormID master indices must be valid
            cat_errors = self._check_formid_masters(cat, reloaded)
            assert not cat_errors, \
                f"Bad FormID masters in YAS_DaedricHelmetAA_CAT:\n" + \
                "\n".join(cat_errors)

        furrify_and_check(write, verify)


class TestNonHeadArmor:
    """Body-only armor should not be modified by furrification."""

    def test_bandit_cuirass_not_in_patch(self, furrify_and_check, plugin_set):
        """ArmorBanditCuirass and BanditCuirassAA are body-only
        and should not appear in the patch."""
        armo = plugin_set.get_record_by_edid('ARMO', 'ArmorBanditCuirass')
        assert armo is not None, "ArmorBanditCuirass not found"
        arma = plugin_set.get_record_by_edid('ARMA', 'BanditCuirassAA')
        assert arma is not None, "BanditCuirassAA not found"
        flags = _get_bodypart_flags(arma)
        assert not flags.Head and not flags.Hair, \
            "BanditCuirassAA should be body-only"


        def write(furry_ctx):
            furry_ctx.merge_armor_overrides(plugin_set)
            furry_ctx.furrify_all_armor(plugin_set)


        def verify(reloaded):
            # Body-only ARMA should not be modified by furrification
            patched_arma = find_by_edid(reloaded, 'BanditCuirassAA')
            assert patched_arma is None, \
                "BanditCuirassAA should NOT be in the patch " \
                "(body-only armor addon, no head/hair slots)"

        furrify_and_check(write, verify)


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
