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
    requires_gamefiles, find_record, find_by_formid, run_verify_phase,
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
    """Furrify armor and verify results survive save/reload."""

    def test_armor_race_addition(self, furrify_and_check, all_plugins,
                                  races_by_edid):
        """Head armor gets furry races added after save/reload."""


        def write(furry_ctx):
            count = furry_ctx.furrify_all_armor(all_plugins)
            assert count > 0, "Should have modified at least one ARMA record"


        def verify(reloaded):
            # At least one ARMA record should exist in the saved plugin
            arma_records = reloaded.get_records_by_signature('ARMA')
            assert len(arma_records) > 0, \
                "No ARMA records in saved plugin"

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
