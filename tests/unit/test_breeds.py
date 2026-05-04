"""Phase 1 + 2 of the breeds feature — registry, substitutability,
headpart filtering.

A breed is a constrained visual flavor of a parent furry race. The
engine sees only the parent race; the breed exists at the furrifier's
NPC-patch layer. See PLAN_FURRIFIER_BREEDS.md.

Phase 1 covers:
- `RaceDefContext.set_breed` registers a breed under its parent race.
- `resolve_race_or_breed(name)` returns `(engine_race_edid, Breed | None)`.
- `roll_breed(npc_alias, parent_race_edid)` deterministically picks a
  breed (or None) based on each breed's `probability`.
- Scheme-load validates that probabilities for one parent sum to ≤ 1.0.
- `breeds = [...]` blocks in `races/*.toml` get loaded into the context.
"""
from __future__ import annotations

import pytest

from furrifier.models import HeadpartRule
from furrifier.race_defs import RaceDefContext


class TestSetBreed:
    def test_set_breed_registers_in_lookup_dict(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.1)
        assert 'Cougar' in ctx.breeds
        assert ctx.breeds['Cougar'].parent_race_edid == 'YASKaloRace'
        assert ctx.breeds['Cougar'].probability == 0.1

    def test_set_breed_indexes_by_parent(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.1)
        ctx.set_breed('Lynx', 'YASKaloRace', probability=0.2)
        ctx.set_breed('CapeBuffalo', 'YASMinoRace', probability=0.0)
        assert [b.name for b in ctx.breeds_by_parent['YASKaloRace']] == [
            'Cougar', 'Lynx']
        assert [b.name for b in ctx.breeds_by_parent['YASMinoRace']] == [
            'CapeBuffalo']

    def test_set_breed_default_probability_is_zero(self):
        """Decision #13: omitted probability means explicit-only."""
        ctx = RaceDefContext()
        ctx.set_breed('CapeBuffalo', 'YASMinoRace')
        assert ctx.breeds['CapeBuffalo'].probability == 0.0

    def test_set_breed_overflow_raises(self):
        """Sum of probabilities for a single parent must be ≤ 1.0."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.6)
        with pytest.raises(ValueError, match="probability"):
            ctx.set_breed('Lynx', 'YASKaloRace', probability=0.5)


class TestResolveRaceOrBreed:
    def test_known_breed_returns_parent_and_breed_obj(self):
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.1)
        engine_race, breed = ctx.resolve_race_or_breed('Cougar')
        assert engine_race == 'YASKaloRace'
        assert breed is not None and breed.name == 'Cougar'

    def test_unknown_name_returns_name_and_none(self):
        """Names that aren't breeds pass through — caller treats them as
        race EDIDs. Race-existence validation happens at session setup."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace')
        engine_race, breed = ctx.resolve_race_or_breed('YASLykaiosRace')
        assert engine_race == 'YASLykaiosRace'
        assert breed is None


class TestRollBreed:
    def test_no_breeds_for_parent_returns_none(self):
        ctx = RaceDefContext()
        assert ctx.roll_breed('Foo', 'YASMinoRace') is None

    def test_zero_probability_breed_never_rolled(self):
        """probability=0 means 'never auto-assigned' — every NPC alias
        across the keyspace lands in the breed-less slice."""
        ctx = RaceDefContext()
        ctx.set_breed('CapeBuffalo', 'YASMinoRace', probability=0.0)
        for i in range(200):
            assert ctx.roll_breed(f'Npc{i}', 'YASMinoRace') is None

    def test_probability_distribution_roughly_matches(self):
        """With probability=0.5 across 500 distinct aliases the Cougar
        count should land within ±20% of the expected 250. The hash is
        deterministic, not statistically uniform — bounds widen enough
        to absorb that without masking a roll bug (e.g. always-pick or
        never-pick would land far outside)."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.5)
        cougars = sum(
            1 for i in range(500)
            if ctx.roll_breed(f'TestNpc{i:03d}', 'YASKaloRace') is not None)
        assert 200 <= cougars <= 300, (
            f"Cougar count {cougars} outside expected 200-300 for p=0.5 "
            f"on 500 samples; check that roll_breed honors the slice")

    def test_roll_is_deterministic(self):
        """Same alias → same breed across calls. The hash-based mechanism
        is what makes test fixtures stable across re-runs."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.5)
        ctx.set_breed('Lynx', 'YASKaloRace', probability=0.5)
        first = [ctx.roll_breed(f'X{i}', 'YASKaloRace') for i in range(50)]
        second = [ctx.roll_breed(f'X{i}', 'YASKaloRace') for i in range(50)]
        assert first == second

    def test_breedless_slice_when_probabilities_dont_fill(self):
        """Probabilities sum to 0.3 → ~70% of NPCs land breed-less. ±20%
        of the expected 350 across 500 samples."""
        ctx = RaceDefContext()
        ctx.set_breed('Cougar', 'YASKaloRace', probability=0.1)
        ctx.set_breed('Lynx', 'YASKaloRace', probability=0.2)
        breedless = sum(
            1 for i in range(500)
            if ctx.roll_breed(f'Foo{i:03d}', 'YASKaloRace') is None)
        assert 280 <= breedless <= 420, (
            f"breed-less count {breedless} outside expected 280-420 "
            f"for total breed probability 0.3 on 500 samples")


class TestBreedFromRaceCatalog:
    def test_breeds_block_loaded_from_races_toml(self, tmp_path, monkeypatch):
        """A `breeds = [...]` block in any races/*.toml lands in the
        context exactly like headpart_equivalents and probability rules
        do — every scheme picks it up via _load_race_catalogs."""
        races_dir = tmp_path / 'races'
        races_dir.mkdir()
        (races_dir / 'test_breeds.toml').write_text(
            'breeds = [\n'
            '    {breed = "Cougar", race = "YASKaloRace", probability = 0.1},\n'
            '    {breed = "CapeBuffalo", race = "YASMinoRace"},\n'
            ']\n'
        )
        schemes_dir = tmp_path / 'schemes'
        schemes_dir.mkdir()
        (schemes_dir / 'tiny.toml').write_text(
            'races = [{vanilla = "NordRace", furry = "YASLykaiosRace"}]\n'
        )
        from furrifier import race_defs
        monkeypatch.setattr(
            race_defs, '_find_resource_dir',
            lambda name: schemes_dir if name == 'schemes' else races_dir)

        ctx = race_defs.load_scheme('tiny')
        assert 'Cougar' in ctx.breeds
        assert 'CapeBuffalo' in ctx.breeds
        assert ctx.breeds['Cougar'].parent_race_edid == 'YASKaloRace'
        assert ctx.breeds['Cougar'].probability == 0.1
        assert ctx.breeds['CapeBuffalo'].probability == 0.0


class TestSchemeBreedSubstitutability:
    """Breed names work anywhere a race name is accepted."""

    def test_breed_in_npc_races(self, tmp_path, monkeypatch):
        races_dir = tmp_path / 'races'
        races_dir.mkdir()
        (races_dir / 'breeds.toml').write_text(
            'breeds = [{breed = "CapeBuffalo", race = "YASMinoRace"}]\n')
        schemes_dir = tmp_path / 'schemes'
        schemes_dir.mkdir()
        (schemes_dir / 's.toml').write_text(
            'races = [{vanilla = "OrcRace", furry = "YASMinoRace"}]\n'
            '[npc_races]\n'
            'UraggroShub = "CapeBuffalo"\n'
        )
        from furrifier import race_defs
        monkeypatch.setattr(
            race_defs, '_find_resource_dir',
            lambda name: schemes_dir if name == 'schemes' else races_dir)

        ctx = race_defs.load_scheme('s')
        assert ctx.npc_races['UraggroShub'] == 'CapeBuffalo'
        engine_race, breed = ctx.resolve_race_or_breed(
            ctx.npc_races['UraggroShub'])
        assert engine_race == 'YASMinoRace'
        assert breed is not None and breed.name == 'CapeBuffalo'

    def test_breed_in_faction_races(self, tmp_path, monkeypatch):
        races_dir = tmp_path / 'races'
        races_dir.mkdir()
        (races_dir / 'breeds.toml').write_text(
            'breeds = [{breed = "Cougar", race = "YASKaloRace"}]\n')
        schemes_dir = tmp_path / 'schemes'
        schemes_dir.mkdir()
        (schemes_dir / 's.toml').write_text(
            'races = [{vanilla = "DarkElfRace", furry = "YASKaloRace"}]\n'
            '[faction_races]\n'
            'CougarsFaction = "Cougar"\n'
        )
        from furrifier import race_defs
        monkeypatch.setattr(
            race_defs, '_find_resource_dir',
            lambda name: schemes_dir if name == 'schemes' else races_dir)

        ctx = race_defs.load_scheme('s')
        engine_race, breed = ctx.resolve_race_or_breed(
            ctx.faction_races['CougarsFaction'])
        assert engine_race == 'YASKaloRace'
        assert breed is not None and breed.name == 'Cougar'

    def test_breed_in_vanilla_furry_mapping(self, tmp_path, monkeypatch):
        races_dir = tmp_path / 'races'
        races_dir.mkdir()
        (races_dir / 'breeds.toml').write_text(
            'breeds = [{breed = "CapeBuffalo", race = "YASMinoRace"}]\n')
        schemes_dir = tmp_path / 'schemes'
        schemes_dir.mkdir()
        (schemes_dir / 's.toml').write_text(
            'races = [{vanilla = "OrcRace", furry = "CapeBuffalo"}]\n')
        from furrifier import race_defs
        monkeypatch.setattr(
            race_defs, '_find_resource_dir',
            lambda name: schemes_dir if name == 'schemes' else races_dir)

        ctx = race_defs.load_scheme('s')
        # The assignment stores the breed name verbatim; resolution
        # happens at use time.
        assert ctx.assignments['OrcRace'].furry_id == 'CapeBuffalo'
        engine_race, breed = ctx.resolve_race_or_breed(
            ctx.assignments['OrcRace'].furry_id)
        assert engine_race == 'YASMinoRace'
        assert breed is not None and breed.name == 'CapeBuffalo'


# ---------------------------------------------------------------------------
# Phase 2 — headpart filtering by breed
# ---------------------------------------------------------------------------


class TestHeadpartRule:
    """`HeadpartRule(probability, headpart_whitelist)` is the unified
    record stored per (race-or-breed, sex, hp_type)."""

    def test_default_rule_is_unconstrained(self):
        r = HeadpartRule()
        assert r.probability == 1.0
        assert r.headpart_whitelist == ()

    def test_rule_carries_whitelist_tuple(self):
        r = HeadpartRule(probability=1.0, headpart_whitelist=('A', 'B'))
        assert r.headpart_whitelist == ('A', 'B')


class TestSetGetHeadpartRule:
    def test_set_rule_stored_per_key(self):
        ctx = RaceDefContext()
        ctx.set_headpart_rule(
            'CapeBuffalo', 'Male', 'EYEBROWS',
            probability=1.0, headpart_whitelist=('BDMinoBisonHorns',))
        rule = ctx.get_headpart_rule('CapeBuffalo', 'Male', 'EYEBROWS')
        assert rule.probability == 1.0
        assert rule.headpart_whitelist == ('BDMinoBisonHorns',)

    def test_breed_inherits_parent_when_silent(self):
        """Decision #5: breed silent on a type → parent race rule applies."""
        ctx = RaceDefContext()
        ctx.set_breed('CapeBuffalo', 'BDMinoRace', probability=0.0)
        ctx.set_headpart_rule(
            'BDMinoRace', 'Male', 'FACIAL_HAIR', probability=0.5)
        # Breed has no FACIAL_HAIR rule → falls through to BDMinoRace.
        rule = ctx.get_headpart_rule('CapeBuffalo', 'Male', 'FACIAL_HAIR')
        assert rule.probability == 0.5

    def test_breed_rule_overrides_parent(self):
        ctx = RaceDefContext()
        ctx.set_breed('CapeBuffalo', 'BDMinoRace')
        ctx.set_headpart_rule(
            'BDMinoRace', 'Male', 'FACIAL_HAIR', probability=0.5)
        ctx.set_headpart_rule(
            'CapeBuffalo', 'Male', 'FACIAL_HAIR', probability=0.0)
        rule = ctx.get_headpart_rule('CapeBuffalo', 'Male', 'FACIAL_HAIR')
        assert rule.probability == 0.0

    def test_unknown_name_returns_default(self):
        ctx = RaceDefContext()
        rule = ctx.get_headpart_rule('NoSuchRace', 'Male', 'EYEBROWS')
        assert rule.probability == 1.0
        assert rule.headpart_whitelist == ()

    def test_get_headpart_probability_still_works(self):
        """Existing public API remains stable — internally it just
        returns the rule's probability."""
        ctx = RaceDefContext()
        ctx.set_headpart_probability('BDMinoRace', 'Male', 'EYEBROWS', 0.7)
        assert ctx.get_headpart_probability(
            'BDMinoRace', 'Male', 'EYEBROWS') == 0.7

    def test_breed_inheritance_for_get_headpart_probability(self):
        """get_headpart_probability is breed-aware via the same
        inheritance chain — so existing _should_assign keeps working."""
        ctx = RaceDefContext()
        ctx.set_breed('CapeBuffalo', 'BDMinoRace')
        ctx.set_headpart_probability('BDMinoRace', 'Male', 'EYEBROWS', 0.7)
        assert ctx.get_headpart_probability(
            'CapeBuffalo', 'Male', 'EYEBROWS') == 0.7


class TestStructuredHeadpartProbabilityLoader:
    """The headpart_probability TOML row's per-type values can be a
    flat float (existing) or a structured table (new)."""

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

    def test_flat_float_value_still_supported(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'headpart_probability = [\n'
            '  {race = "BDMinoRace", sex = "Male", EYEBROWS = 0.4},\n'
            ']\n')
        rule = ctx.get_headpart_rule('BDMinoRace', 'Male', 'EYEBROWS')
        assert rule.probability == 0.4
        assert rule.headpart_whitelist == ()

    def test_structured_value_with_whitelist(self, tmp_path, monkeypatch):
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'breeds = [{breed = "WhiteTail", race = "BDDeerRace"}]\n'
            'headpart_probability = [\n'
            '  {race = "WhiteTail", sex = "Male", '
            'EYEBROWS = {probability = 1.0, headpart = ["BDDeerHorns1"]},'
            '   FACIAL_HAIR = 0.0},\n'
            ']\n')
        eb = ctx.get_headpart_rule('WhiteTail', 'Male', 'EYEBROWS')
        assert eb.probability == 1.0
        assert eb.headpart_whitelist == ('BDDeerHorns1',)
        # Sibling FACIAL_HAIR uses the flat-float form on the same row.
        fh = ctx.get_headpart_rule('WhiteTail', 'Male', 'FACIAL_HAIR')
        assert fh.probability == 0.0

    def test_structured_value_omits_probability_defaults_to_one(
            self, tmp_path, monkeypatch):
        """Decision #3: missing `probability` on a structured value
        means 1.0 (always apply)."""
        ctx = self._load_with_data(tmp_path, monkeypatch,
            'breeds = [{breed = "WhiteTail", race = "BDDeerRace"}]\n'
            'headpart_probability = [\n'
            '  {race = "WhiteTail", sex = "Male", '
            'EYEBROWS = {headpart = ["BDDeerHorns1"]}},\n'
            ']\n')
        rule = ctx.get_headpart_rule('WhiteTail', 'Male', 'EYEBROWS')
        assert rule.probability == 1.0
        assert rule.headpart_whitelist == ('BDDeerHorns1',)
