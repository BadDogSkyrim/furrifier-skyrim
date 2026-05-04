"""Breed tint registry: get/set + breed-to-parent inheritance.

The TOML loader path lives in test_color_schemes.py — this file
covers the in-memory `set_tint_rules` / `get_tint_rules` semantics,
which any breed-aware code path leans on. See PLAN_FURRIFIER_BREEDS.md.
"""
from __future__ import annotations

import pytest

from furrifier.models import BreedTintRule
from furrifier.race_defs import RaceDefContext


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
        assert r.color_choices == (('Cinnamon', 0.8), ('Sable', 0.6))


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
        assert rules[0].color_choices == (('CougarSpecific', 1.0),)

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
