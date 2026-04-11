"""Tests for tint layer logic."""

from furrifier.tints import (
    class_name_to_layer, choose_tint_preset,
    _randomize_index_list, TINT_CLASS_NAMES,
)
from furrifier.furry_load import _classify_tint_path
from furrifier.models import TintLayer


class TestClassNameToLayer:
    def test_skin_tone(self):
        assert class_name_to_layer('Skin Tone') == 0

    def test_muzzle(self):
        assert class_name_to_layer('Muzzle') == TintLayer.MUZZLE

    def test_mustache(self):
        assert class_name_to_layer('Mustache') == TintLayer.MUSTACHE

    def test_mustache_is_fur_layer(self):
        # Mustache must live in the fur range so it's applied unconditionally,
        # not treated as a decoration that only shows up if the vanilla NPC
        # already has it.
        assert TintLayer.MUSTACHE < TintLayer.DECORATION_LO

    def test_unknown(self):
        assert class_name_to_layer('Nonexistent') == -1

    def test_all_names_resolve(self):
        for i, name in enumerate(TINT_CLASS_NAMES):
            assert class_name_to_layer(name) == i


class TestClassifyTintPath:
    """Path-to-class-name classification, esp. the Mustache/Muzzle split."""

    def test_mustache_is_its_own_class(self):
        # YAS Kettu male mustache path — must land in Mustache, not Muzzle,
        # so it's always applied instead of competing with Muzzle assets.
        assert _classify_tint_path(
            r'YAS\Kettu\Male\tints\Mustache01.dds') == 'Mustache'

    def test_moustache_alt_spelling(self):
        # YAS Kettu female uses the British spelling.
        assert _classify_tint_path(
            r'YAS\Dog\Tints\FemMoustacheTint.dds') == 'Mustache'

    def test_muzzle_still_muzzle(self):
        # Plain Muzzle textures must still classify as Muzzle.
        assert _classify_tint_path(
            r'YAS\Kettu\Male\tints\Muzzle01.dds') == 'Muzzle'
        assert _classify_tint_path(
            r'YAS\Dog\Tints\FemMuzzle05Tint.dds') == 'Muzzle'


class TestChooseTintPreset:
    def test_single_preset(self):
        idx = choose_tint_preset('NPC', 0, [(0, 1.0)])
        assert idx == 0

    def test_skip_first(self):
        presets = [(0, 1.0), (1, 0.5), (2, 0.8)]
        idx = choose_tint_preset('NPC', 0, presets, skip_first=True)
        assert idx >= 1  # Should never pick index 0

    def test_empty_presets(self):
        idx = choose_tint_preset('NPC', 0, [])
        assert idx is None

    def test_deterministic(self):
        presets = [(i, 0.5) for i in range(10)]
        r1 = choose_tint_preset('Lydia', 1455, presets)
        r2 = choose_tint_preset('Lydia', 1455, presets)
        assert r1 == r2

    def test_different_npcs_differ(self):
        """Different NPCs should usually get different presets."""
        presets = [(i, 0.5) for i in range(20)]
        results = set()
        for name in ['Lydia', 'Ulfric', 'Delphine', 'Nazeem', 'Balgruuf']:
            results.add(choose_tint_preset(name, 1455, presets))
        assert len(results) > 1  # Not all the same


class TestRandomizeIndexList:
    def test_length(self):
        result = _randomize_index_list('test', 0, 5)
        # May be shorter than 5 if hash collisions occur
        assert len(result) <= 5
        assert len(result) > 0

    def test_deterministic(self):
        r1 = _randomize_index_list('Lydia', 5345, 10)
        r2 = _randomize_index_list('Lydia', 5345, 10)
        assert r1 == r2

    def test_contains_valid_indices(self):
        result = _randomize_index_list('test', 0, 5)
        for idx in result:
            assert 0 <= idx < 5

    def test_empty(self):
        assert _randomize_index_list('test', 0, 0) == []

    def test_single(self):
        result = _randomize_index_list('test', 0, 1)
        assert result == [0]
