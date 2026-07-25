"""Breed tint registry: get/set + breed-to-parent inheritance.

The TOML loader path lives in test_color_schemes.py — this file
covers the in-memory `set_tint_rules` / `get_tint_rules` semantics,
which any breed-aware code path leans on. See PLAN_FURRIFIER_BREEDS.md.
"""
from __future__ import annotations

import pytest

from furrifier.models import BreedTintRule, TintAsset
from furrifier.race_defs import RaceDefContext
from furrifier.tints import RaceTintData, pick_uncovered_decorations


class TestBreedTintRule:
    def test_default_probability_is_one(self):
        r = BreedTintRule(mask_substring='SkinTone',
                          color_choices=(('A', 1.0),))
        assert r.probability == 1.0

    def test_carries_color_choices_as_tuple_of_pairs(self):
        r = BreedTintRule(mask_substring='Spots',
                          color_choices=(('Cinnamon', 0.8),
                                         ('Sable', 0.6)),
                          probability=0.5)
        assert r.color_choices == (('Cinnamon', (0.8, 0.8)), ('Sable', (0.6, 0.6)))


class TestSetGetTintRules:
    def test_silent_returns_none(self):
        """A breed with no tint rules registered → get_tint_rules
        returns None, signalling 'inherit from parent / use the
        unconstrained pool'."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        assert ctx.get_tint_rules('Cougar', 'Male') is None

    def test_explicit_empty_list_returns_empty_list(self):
        """An explicit empty list is 'no tints applied' — distinct
        from silence (decision #2)."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        ctx.set_tint_rules('Cougar', 'Male', [])
        assert ctx.get_tint_rules('Cougar', 'Male') == []

    def test_returns_registered_rules(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        rule = BreedTintRule(mask_substring='SkinTone',
                             color_choices=(('PantherSkinTan', 1.0),))
        ctx.set_tint_rules('Cougar', 'Male', [rule])
        assert ctx.get_tint_rules('Cougar', 'Male') == [rule]

    def test_breed_inherits_parent_tint_rules_when_silent(self):
        """Decision #5: silent breed inherits parent race's tint rules.
        (Races rarely define their own, but if they do, breeds defer.)"""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        rule = BreedTintRule(mask_substring='SkinTone',
                             color_choices=(('GenericSkin', 1.0),))
        ctx.set_tint_rules('YASKaloRace', 'Male', [rule])
        assert ctx.get_tint_rules('Cougar', 'Male') == [rule]

    def test_breed_explicit_overrides_parent(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        ctx.set_tint_rules('YASKaloRace', 'Male', [
            BreedTintRule(mask_substring='SkinTone',
                          color_choices=(('Inherited', 1.0),))])
        ctx.set_tint_rules('Cougar', 'Male', [
            BreedTintRule(mask_substring='SkinTone',
                          color_choices=(('CougarSpecific', 1.0),))])
        rules = ctx.get_tint_rules('Cougar', 'Male')
        assert rules is not None
        assert rules[0].color_choices == (('CougarSpecific', (1.0, 1.0)),)

    def test_sex_specific_then_sex_agnostic(self):
        """Sex fallback: (name, 'Male') → (name, None)."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        rule = BreedTintRule(mask_substring='X',
                             color_choices=(('A', 1.0),))
        ctx.set_tint_rules('Cougar', None, [rule])
        # Asking for Male should fall through to the sex-agnostic entry.
        assert ctx.get_tint_rules('Cougar', 'Male') == [rule]

    def test_breed_explicit_empty_does_not_inherit(self):
        """Explicit empty is zero; do not fall through to parent."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        ctx.set_tint_rules('YASKaloRace', 'Male', [
            BreedTintRule(mask_substring='X',
                          color_choices=(('A', 1.0),))])
        ctx.set_tint_rules('Cougar', 'Male', [])
        assert ctx.get_tint_rules('Cougar', 'Male') == []


class TestPickUncoveredDecorations:
    """The breed-driven path runs only the rules a breed authors. For
    decoration classes the breed didn't author rules for, this helper
    fills in choices when the vanilla NPC wore one of that class —
    so a Mino-LongHorn version of a vanilla skull-painted Orc still
    gets a Skull paint, even though LongHorn's tint rules only
    enumerate fur layers.

    Failure mode this guards against (surfaced 2026-05-06 on
    DA06LvlOrcMelee): vanilla NPC has Skull → patched breed-tagged
    NPC has none, because LongHorn's rules don't mention Skull.
    """

    def _asset(self, index, filename, layer_class, presets=None):
        return TintAsset(
            index=index, filename=filename, layer_type=0,
            layer_class=layer_class,
            presets=presets if presets is not None else [(0x123, 1.0, 0)],
        )

    def _race_data(self, **classes):
        d = RaceTintData()
        d.classes = classes
        return d


    def test_emits_uncovered_decoration_when_npc_had_one(self):
        race = self._race_data(
            **{
                'Skin Tone': [self._asset(1, 'SkinTone.dds', 'Skin Tone')],
                'Skull': [self._asset(24, 'BDHorseSkull.dds', 'Skull')],
            })
        rules = [BreedTintRule(
            mask_substring='SkinTone',
            color_choices=(('Brown', 1.0),))]
        choices = pick_uncovered_decorations(
            'TestNPC', rules, race, npc_tint_classes={'Skull'})
        assert len(choices) == 1
        assert choices[0].tini == 24


    def test_skips_decoration_when_npc_didnt_have_it(self):
        """No vanilla → no fallback. Author intent on the breed
        side wins for absent decorations."""
        race = self._race_data(
            **{
                'Skin Tone': [self._asset(1, 'SkinTone.dds', 'Skin Tone')],
                'Skull': [self._asset(24, 'BDHorseSkull.dds', 'Skull')],
            })
        rules = [BreedTintRule(
            mask_substring='SkinTone',
            color_choices=(('Brown', 1.0),))]
        choices = pick_uncovered_decorations(
            'TestNPC', rules, race, npc_tint_classes={'Skin Tone'})
        # Skin Tone is fur, not decoration — and it's already covered
        # by the rule anyway. No decoration emitted.
        assert choices == []


    def test_skips_decoration_when_breed_already_covers_it(self):
        """A breed rule whose mask_substring matches a Skull asset's
        filename means the breed has authored intent over Skull —
        don't double-emit by also picking from the standard pool."""
        race = self._race_data(
            **{
                'Skull': [self._asset(24, 'BDHorseSkull.dds', 'Skull')],
            })
        rules = [BreedTintRule(
            mask_substring='Skull',  # matches the Skull asset
            color_choices=(('Black', 1.0),))]
        choices = pick_uncovered_decorations(
            'TestNPC', rules, race, npc_tint_classes={'Skull'})
        assert choices == []


    def test_per_asset_coverage_picks_uncovered_siblings(self):
        """Coverage is per-asset, not per-class. If a breed rule
        happens to match one asset in a class (e.g. a typo'd
        TintCheeckLower mask that mis-classifies as Paint), the
        OTHER 7 Paint assets in the same class are still eligible
        for fallback. Otherwise one false-classified asset would
        silence the entire decoration class.

        Real-world example: BDMinotaur OrcRace 'Paint' contains
        slot 622 (`TintCheeckLower.dds`) plus slots 23, 417, 434,
        451, 468, 485 (`BDHorseWarpaintNN.dds` etc). LongHorn rule
        `mask_substring='TintCheeckLower'` matches slot 622 only;
        the others must remain pickable.
        """
        race = self._race_data(
            **{'Paint': [
                self._asset(622, 'TintCheeckLower.dds', 'Paint'),
                self._asset(23, 'BDHorseWarpaint01.dds', 'Paint'),
                self._asset(485, 'BDDeerHoofprint.dds', 'Paint'),
            ]})
        rules = [BreedTintRule(
            mask_substring='TintCheeckLower',
            color_choices=(('Brown', 0.5),))]
        choices = pick_uncovered_decorations(
            'TestNPC', rules, race, npc_tint_classes={'Paint'})
        # One layer emitted, NOT slot 622 (covered by the rule).
        assert len(choices) == 1
        assert choices[0].tini in (23, 485)


    def test_skips_fur_classes_even_when_uncovered(self):
        """Fur layers are the breed's domain. The breed lists the
        fur it wants; we don't substitute from the parent pool."""
        race = self._race_data(
            **{
                'Muzzle': [self._asset(9, 'TintMuzzle.dds', 'Muzzle')],
            })
        rules = []  # breed authored nothing
        # vanilla had Muzzle but the breed doesn't list any rules —
        # this helper still skips because Muzzle is a fur class
        choices = pick_uncovered_decorations(
            'TestNPC', rules, race, npc_tint_classes={'Muzzle'})
        assert choices == []


    def test_no_rules_no_op(self):
        """A breed with no tint rules at all shouldn't lose the fast
        path of returning [] immediately."""
        race = self._race_data(
            **{'Skull': [self._asset(24, 'BDHorseSkull.dds', 'Skull')]})
        choices = pick_uncovered_decorations(
            'TestNPC', [], race, npc_tint_classes={'Skull'})
        assert choices == []
