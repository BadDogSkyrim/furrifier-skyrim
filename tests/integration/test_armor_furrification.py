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

        orc = plugin_set.get_record_by_edid('RACE', 'OrcRace')
        assert orc is not None, "OrcRace not loaded"
        orc_obj = orc.form_id.object_index


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
            assert orc_obj in modl_objs, \
                f"OrcRace ({hex(orc_obj)}) should be ADDED to " \
                f"BDStormcloakHelm_CAT (cat mesh, fits furrified Orcs)"

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


class TestObjectIndexCollisions:
    """Races sharing an object index across plugins must stay distinct.

    CellanRace (CellanRace.esp) and YASKaloRace (BDCatRaces.esp) are both
    at object index 000800; YASShanRace and BDHorseRace are both at
    000804. furrify_all_armor keys its race maps on the full normalized
    FormID so these never merge.

    The collision is currently LATENT: every ARMA carrying CellanRace also
    carries KhajiitRace, and the fallback path supplies the same vanilla
    races either way, so merging them had no observable effect. Do not add
    a test asserting DarkElfRace is absent from Cellan ARMAs -- it is
    there legitimately, via the KhajiitRace fallback, on 156 of the 229
    ARMAs that carry KhajiitRace.
    """

    def test_the_collision_still_exists(self, plugin_set):
        """Marker: if these races stop colliding, the keying above stops
        being load-bearing and this class can go."""
        cellan = plugin_set.get_record_by_edid('RACE', 'CellanRace')
        kalo = plugin_set.get_record_by_edid('RACE', 'YASKaloRace')
        assert cellan is not None, "CellanRace.esp not loaded"
        assert kalo is not None, "BDCatRaces.esp not loaded"
        assert cellan.form_id.object_index == kalo.form_id.object_index, (
            "CellanRace and YASKaloRace no longer collide on object index")

    def test_no_duplicate_arma_form_ids(self, furrify_and_check, plugin_set):
        """No two ARMA records in the patch may share a FormID.

        The armor pass and the schlong pass both copy sheath ARMAs. When
        each copied unconditionally the patch ended up with two records
        per FormID (100 of 195 ARMAs in one shipped build); the engine
        reads whichever it reaches first, so one pass's work was lost.

        ARMA only, and verify-only: ARMO duplicates are an artifact of
        this file calling merge_armor_overrides once per test against a
        shared patch, which production never does.
        """
        def write(furry_ctx):
            pass  # deliberately no-op -- see test_cellan_... docstring

        def verify(reloaded):
            seen: dict[int, str] = {}
            dupes: list[str] = []
            for rec in reloaded.get_records_by_signature('ARMA'):
                fid = rec.form_id.value
                if fid in seen:
                    dupes.append(f"{fid:08X} {rec.editor_id!r} "
                                 f"(also {seen[fid]!r})")
                else:
                    seen[fid] = rec.editor_id or '?'
            assert not dupes, (
                f"{len(dupes)} duplicate ARMA FormID(s) in the patch:\n  "
                + "\n  ".join(dupes[:20]))

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
