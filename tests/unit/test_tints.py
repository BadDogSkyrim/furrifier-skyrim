"""Tests for tint layer logic."""

from furrifier.tints import (
    class_name_to_layer, choose_tint_preset, choose_breed_tints,
    _randomize_index_list, RaceTintData, TINT_CLASS_NAMES,
)
from furrifier.furry_load import _classify_tint_path
from furrifier.models import BreedTintRule, TintAsset, TintLayer


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

    def test_vanilla_khajiit_paint_is_warpaint(self):
        # KhajiitPaint01-04 are the vanilla Khajiit warpaint masks.
        # They must classify as 'Paint' (the warpaint class — index 31
        # in TINT_CLASS_NAMES, in the decoration range, only applied
        # when the NPC already has them) so they composite the right
        # way. Regression for the long-standing concern that they
        # might get tagged as e.g. 'Skin Tone' or 'Dirt' and break the
        # blend stack.
        for n in ('01', '02', '03', '04'):
            path = (
                f'textures/actors/character/character assets/'
                f'tintmasks/khajiitpaint{n}.dds')
            assert _classify_tint_path(path) == 'Paint', (
                f'{path} misclassified')

    def test_vanilla_khajiit_full_lockdown(self):
        # Lock down every vanilla Khajiit tint mask under
        # textures/actors/character/character assets/tintmasks/. A
        # future tweak to the keyword table can't silently drift any
        # of these without the test failing. Also pins the
        # CheekColorLower / EyeSocketUpper fixes from 2026-05-06 so
        # they don't regress (the bare "Cheek" / "EyeSocket"
        # catch-alls used to win against vanilla's underscore-free
        # filenames).
        cases = {
            'khajiitcheekcolor.dds': 'Cheek Color',
            'khajiitcheekcolorlower.dds': 'Cheek Color Lower',
            'khajiitchin.dds': 'Chin',
            'khajiitdirt.dds': 'Dirt',
            'khajiiteyeliner.dds': 'Eyeliner',
            # No Upper/Lower suffix: falls through to the generic
            # 'EyeSocket' keyword, which historically maps to Lower.
            'khajiiteyesocket01.dds': 'EyeSocket Lower',
            'khajiiteyesocket02.dds': 'EyeSocket Lower',
            'khajiiteyesocketlower.dds': 'EyeSocket Lower',
            'khajiiteyesocketupper.dds': 'EyeSocket Upper',
            'khajiitforehead.dds': 'Forehead',
            'khajiitlaughlines.dds': 'Laugh Lines',
            'khajiitlipcolor.dds': 'Lip Color',
            'khajiitneck.dds': 'Neck',
            'khajiitnose01.dds': 'Nose',
            'khajiitpaint01.dds': 'Paint',
            'khajiitpaint02.dds': 'Paint',
            'khajiitpaint03.dds': 'Paint',
            'khajiitpaint04.dds': 'Paint',
            'khajiitstripes01.dds': 'Stripes',
            'khajiitstripes02.dds': 'Stripes',
            'khajiitstripes03.dds': 'Stripes',
            'khajiitstripes04.dds': 'Stripes',
        }
        for filename, expected in cases.items():
            path = (
                f'textures/actors/character/character assets/'
                f'tintmasks/{filename}')
            assert _classify_tint_path(path) == expected, (
                f'{filename} expected {expected!r}, '
                f'got {_classify_tint_path(path)!r}')


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


class TestChooseBreedTints:
    def test_mask_substring_case_insensitive(self):
        """Scheme-supplied mask substring matches TINT filename
        case-insensitively, so authors don't have to memorize asset
        capitalization. (Hugh hit it twice with TintLaughLine vs
        TintLaughline.dds — see project_furrifier_todo.md item 4.)"""
        asset = TintAsset(
            index=5,
            filename=r'YAS\Deer\Tints\TintLaughline.dds',
            layer_type=0,
            layer_class='Laugh Lines',
            presets=[(0x12345, 1.0, 0)],
        )
        race_data = RaceTintData()
        race_data.classes = {'Laugh Lines': [asset]}
        rule = BreedTintRule(
            mask_substring='TintLaughLine',
            color_choices=(('SomeColor', 0.8),),
            probability=1.0,
        )
        choices = choose_breed_tints(
            'TestNPC', [rule], race_data,
            form_id_for_edid=lambda edid: (
                0x12345 if edid == 'SomeColor' else None),
        )
        assert len(choices) == 1
        assert choices[0].tini == 5
        assert choices[0].tinv == 0.8


class TestRandomizeIndexList:
    def test_is_full_permutation(self):
        """Every input index appears exactly once."""
        result = _randomize_index_list('test', 0, 5)
        assert sorted(result) == [0, 1, 2, 3, 4]

    def test_deterministic(self):
        r1 = _randomize_index_list('Lydia', 5345, 10)
        r2 = _randomize_index_list('Lydia', 5345, 10)
        assert r1 == r2

    def test_different_inputs_differ(self):
        r1 = _randomize_index_list('Lydia', 5345, 10)
        r2 = _randomize_index_list('Ulfric', 5345, 10)
        assert r1 != r2

    def test_empty(self):
        assert _randomize_index_list('test', 0, 0) == []

    def test_single(self):
        assert _randomize_index_list('test', 0, 1) == [0]
