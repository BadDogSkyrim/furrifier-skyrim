"""Color-scheme loader + new BreedTintRule shape.

A scheme is `[color_schemes.NAME]` with one row per tint mask:
    Mask = [[edid, intensity], [edid, intensity], ...]

Plus an optional `["probability", X]` magic row gating layer-application
(default 1.0). Single-element entries `[edid]` default to intensity 1.0.

Picking among non-magic entries is **uniform** (each entry equally
likely) — duplicate EDIDs at different intensities are intentional
(e.g. Tan-strong vs Tan-soft as two equally-likely outcomes).

A `[[headpart_probability]]` row references a scheme by name with
`colors = "SchemeName"`.
"""
from __future__ import annotations

import pytest

from furrifier.models import BreedTintRule
from furrifier.race_defs import RaceDefContext


class TestBreedTintRuleShape:
    def test_color_choices_is_pair_tuple(self):
        r = BreedTintRule(
            mask_substring='SkinTone',
            color_choices=(('A', 1.0), ('B', 0.5)),
        )
        assert r.color_choices == (('A', 1.0), ('B', 0.5))

    def test_default_probability_is_one(self):
        r = BreedTintRule(mask_substring='X', color_choices=(('A', 1.0),))
        assert r.probability == 1.0


class TestColorSchemeLoader:
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

    def test_color_scheme_parses_into_rules(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[color_schemes.CapeBuffalo]\n'
            'SkinTone = [["A", 1.0], ["B", 1.0]]\n'
            'TintMuzzle = [["probability", 0.5], ["C", 1.0], ["D", 0.7]]\n'
        )
        scheme = ctx.color_schemes['CapeBuffalo']
        assert len(scheme) == 2

        skin = next(r for r in scheme if r.mask_substring == 'SkinTone')
        assert skin.color_choices == (('A', 1.0), ('B', 1.0))
        assert skin.probability == 1.0

        muzzle = next(r for r in scheme if r.mask_substring == 'TintMuzzle')
        assert muzzle.color_choices == (('C', 1.0), ('D', 0.7))
        assert muzzle.probability == 0.5

    def test_single_element_entry_defaults_intensity_to_one(
            self, tmp_path, monkeypatch):
        """Hugh's Cattle.SkinTone uses bare `[edid]` to mean 'pick this
        color at full intensity' — same shape as `[edid, 1.0]`."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[color_schemes.Cattle]\n'
            'SkinTone = [["A"], ["B"], ["C", 0.7]]\n'
        )
        scheme = ctx.color_schemes['Cattle']
        rule = scheme[0]
        assert rule.color_choices == (('A', 1.0), ('B', 1.0), ('C', 0.7))

    def test_probability_magic_row_default_one_when_absent(
            self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[color_schemes.X]\n'
            'M = [["A", 1.0]]\n'
        )
        rule = ctx.color_schemes['X'][0]
        assert rule.probability == 1.0

    def test_duplicate_color_edids_preserved(self, tmp_path, monkeypatch):
        """Same EDID at different intensities = two different sample
        points, each picked with equal probability."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[color_schemes.Ankole]\n'
            'TintMuzzle = [["Tan", 0.8], ["Tan", 0.6], ["White", 0.5]]\n'
        )
        rule = ctx.color_schemes['Ankole'][0]
        assert rule.color_choices == (
            ('Tan', 0.8), ('Tan', 0.6), ('White', 0.5))

    def test_multiple_schemes_in_one_file(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[color_schemes.A]\n'
            'M = [["x", 1.0]]\n'
            '[color_schemes.B]\n'
            'M = [["y", 1.0]]\n'
        )
        assert 'A' in ctx.color_schemes and 'B' in ctx.color_schemes
        assert ctx.color_schemes['A'][0].color_choices == (('x', 1.0),)
        assert ctx.color_schemes['B'][0].color_choices == (('y', 1.0),)


class TestColorsReferenceOnHeadpartProbability:
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

    def test_colors_reference_resolves_to_scheme_rules(
            self, tmp_path, monkeypatch):
        """A `colors = "SchemeName"` field on a [[headpart_probability]]
        row pulls the named scheme's rules into this (race, sex) slot."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'breeds = [{breed = "CapeBuffalo", race = "BDMinoRace"}]\n'
            '[color_schemes.CapeBuffalo]\n'
            'SkinTone = [["BDMinoCoatBlack", 1.0]]\n'
            '[[headpart_probability]]\n'
            'race = "CapeBuffalo"\n'
            'sex = "both"\n'
            'colors = "CapeBuffalo"\n'
        )
        rules = ctx.get_tint_rules('CapeBuffalo', 'Male')
        assert rules is not None and len(rules) == 1
        assert rules[0].mask_substring == 'SkinTone'
        assert rules[0].color_choices == (('BDMinoCoatBlack', 1.0),)


class TestSexNormalization:
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

    def test_sex_both_means_sex_agnostic(self, tmp_path, monkeypatch):
        """`sex = "both"` registers under (race, None, type) — applies
        to either sex through the existing fallback chain."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[[headpart_probability]]\n'
            'race = "X"\n'
            'sex = "both"\n'
            'EYEBROWS = 0.4\n'
        )
        # Both 'Male' and 'Female' lookups should hit the same rule.
        assert ctx.get_headpart_probability('X', 'Male', 'EYEBROWS') == 0.4
        assert ctx.get_headpart_probability('X', 'Female', 'EYEBROWS') == 0.4

    def test_sex_omitted_means_sex_agnostic(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[[headpart_probability]]\n'
            'race = "X"\n'
            'EYEBROWS = 0.3\n'
        )
        assert ctx.get_headpart_probability('X', 'Male', 'EYEBROWS') == 0.3
        assert ctx.get_headpart_probability('X', 'Female', 'EYEBROWS') == 0.3

    def test_sex_lowercase_male_female(self, tmp_path, monkeypatch):
        """Lowercase `male` / `female` normalize to internal Male/Female."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[[headpart_probability]]\n'
            'race = "X"\n'
            'sex = "male"\n'
            'EYEBROWS = 0.5\n'
            '[[headpart_probability]]\n'
            'race = "X"\n'
            'sex = "female"\n'
            'EYEBROWS = 0.1\n'
        )
        assert ctx.get_headpart_probability('X', 'Male', 'EYEBROWS') == 0.5
        assert ctx.get_headpart_probability('X', 'Female', 'EYEBROWS') == 0.1

    def test_sex_capitalized_still_works(self, tmp_path, monkeypatch):
        """Existing files using `sex = "Male"` keep parsing — the
        normalizer is case-insensitive."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            '[[headpart_probability]]\n'
            'race = "X"\n'
            'sex = "Male"\n'
            'EYEBROWS = 0.7\n'
        )
        assert ctx.get_headpart_probability('X', 'Male', 'EYEBROWS') == 0.7
