"""Phase 3 of breeds: tint filtering by breed.

A breed's `tints` list is exhaustive — only those layers get emitted on
patched NPCs. Each rule names a mask substring (matched against the
parent race's TINI filename), a list of allowed CLFM EditorIDs, and a
probability. See PLAN_FURRIFIER_BREEDS.md.

Phase 3 deliverable:
- BreedTintRule dataclass + tint_rules registry on RaceDefContext.
- Loader for `tints = [...]` block on a headpart_probability row.
- `get_tint_rules(name, sex)` with breed→parent inheritance and a
  None / [] / [...] semantic that distinguishes silence from "explicit
  empty tints".
"""
from __future__ import annotations

import pytest

from furrifier.models import BreedTintRule
from furrifier.race_defs import RaceDefContext


class TestBreedTintRule:
    def test_default_probability_is_one(self):
        r = BreedTintRule(mask_substring='SkinTone', color_edids=('A',))
        assert r.probability == 1.0

    def test_carries_color_edids_as_tuple(self):
        r = BreedTintRule(mask_substring='Spots',
                          color_edids=('Cinnamon', 'Sable'),
                          probability=0.5)
        assert r.color_edids == ('Cinnamon', 'Sable')


class TestSetGetTintRules:
    def test_silent_returns_none(self):
        """A breed with no `tints` rule registered → get_tint_rules
        returns None, signalling 'inherit from parent / use the
        unconstrained pool'."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        assert ctx.get_tint_rules('Cougar', 'Male') is None

    def test_explicit_empty_list_returns_empty_list(self):
        """A breed with `tints = []` is explicit 'no tints applied' —
        distinct from silence."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        ctx.set_tint_rules('Cougar', 'Male', [])
        rules = ctx.get_tint_rules('Cougar', 'Male')
        assert rules == []

    def test_returns_registered_rules(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        rule = BreedTintRule(mask_substring='SkinTone',
                             color_edids=('PantherSkinTan',))
        ctx.set_tint_rules('Cougar', 'Male', [rule])
        assert ctx.get_tint_rules('Cougar', 'Male') == [rule]

    def test_breed_inherits_parent_tint_rules_when_silent(self):
        """Decision #5: silent breed inherits parent race's tint rules.
        (Races rarely define their own, but if they do, breeds defer.)"""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        rule = BreedTintRule(mask_substring='SkinTone',
                             color_edids=('GenericSkin',))
        ctx.set_tint_rules('YASKaloRace', 'Male', [rule])
        assert ctx.get_tint_rules('Cougar', 'Male') == [rule]

    def test_breed_explicit_overrides_parent(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        ctx.set_tint_rules('YASKaloRace', 'Male', [
            BreedTintRule(mask_substring='SkinTone',
                          color_edids=('Inherited',))])
        ctx.set_tint_rules('Cougar', 'Male', [
            BreedTintRule(mask_substring='SkinTone',
                          color_edids=('CougarSpecific',))])
        rules = ctx.get_tint_rules('Cougar', 'Male')
        assert rules is not None and rules[0].color_edids == ('CougarSpecific',)

    def test_sex_specific_then_sex_agnostic(self):
        """Sex fallback: (name, 'Male') → (name, None)."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        rule = BreedTintRule(mask_substring='X', color_edids=('A',))
        ctx.set_tint_rules('Cougar', None, [rule])
        # Asking for Male should fall through to the sex-agnostic entry.
        assert ctx.get_tint_rules('Cougar', 'Male') == [rule]

    def test_breed_explicit_empty_does_not_inherit(self):
        """tints=[] is explicit zero; do not fall through to parent."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        ctx.set_tint_rules('YASKaloRace', 'Male', [
            BreedTintRule(mask_substring='X', color_edids=('A',))])
        ctx.set_tint_rules('Cougar', 'Male', [])
        assert ctx.get_tint_rules('Cougar', 'Male') == []


class TestTintRulesLoader:
    """`tints = [...]` block on a headpart_probability row gets loaded
    into the rule registry."""

    def _load_with_data(self, tmp_path, monkeypatch, races_toml: str):
        races_dir = tmp_path / 'races'
        races_dir.mkdir()
        (races_dir / 'r.toml').write_text(races_toml)
        schemes_dir = tmp_path / 'schemes'
        schemes_dir.mkdir()
        (schemes_dir / 's.toml').write_text(
            'races = [{vanilla = "NordRace", furry = "Z"}]\n')
        from furrifier import race_defs
        monkeypatch.setattr(
            race_defs, '_find_resource_dir',
            lambda name: schemes_dir if name == 'schemes' else races_dir)
        return race_defs.load_scheme('s')

    def test_tints_block_parses_into_rules(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'breeds = [{breed = "Cougar", race = "YASKaloRace"}]\n'
            'headpart_probability = [\n'
            '  {race = "Cougar", sex = "Male", tints = [\n'
            '    {mask = "SkinTone", colors = ["PantherSkinTan", "PantherSkinYellow"], probability = 1.0},\n'
            '    {mask = "Spots", colors = ["BlackSpots"], probability = 0.5},\n'
            '  ]},\n'
            ']\n')
        rules = ctx.get_tint_rules('Cougar', 'Male')
        assert rules is not None
        assert len(rules) == 2
        assert rules[0].mask_substring == 'SkinTone'
        assert rules[0].color_edids == ('PantherSkinTan', 'PantherSkinYellow')
        assert rules[0].probability == 1.0
        assert rules[1].mask_substring == 'Spots'
        assert rules[1].probability == 0.5

    def test_tints_default_probability_is_one(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'breeds = [{breed = "Cougar", race = "YASKaloRace"}]\n'
            'headpart_probability = [\n'
            '  {race = "Cougar", sex = "Male", tints = ['
            '    {mask = "SkinTone", colors = ["A"]}'
            '  ]},\n'
            ']\n')
        rules = ctx.get_tint_rules('Cougar', 'Male')
        assert rules and rules[0].probability == 1.0

    def test_explicit_empty_tints_distinct_from_missing(
            self, tmp_path, monkeypatch):
        """tints = [] in TOML should register an empty list (explicit
        no-tints), not be confused with absence."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'breeds = [{breed = "Cougar", race = "YASKaloRace"}]\n'
            'headpart_probability = [\n'
            '  {race = "Cougar", sex = "Male", tints = []},\n'
            ']\n')
        assert ctx.get_tint_rules('Cougar', 'Male') == []
