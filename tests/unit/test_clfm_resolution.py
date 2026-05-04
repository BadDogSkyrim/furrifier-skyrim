"""Phase 4 of breeds: CLFM resolution by FormID and EditorID.

The pre-Phase-4 `_resolve_color` matched CLFMs by lower-24-bits of the
FormID. That conflates records that share an object index across
different masters — a real production hazard: in Hugh's load order
0x0012DD collides between `CellanRace.esp` (`CellanFur06GrayBrown`)
and `EnhancedCharacterEdit.esp` (`Red01`).

Phase 4 builds two indexes at session load:
- `_clfm_by_form_id_cache` keyed by full load-order-normalized FormID
- `_clfm_by_edid_cache` keyed by EditorID (load-order winner)

…and refactors `_resolve_color` to use the form-id index.

These unit tests use lightweight mocks so they run without the full
plugin_set machinery. The integration test suite (test_breeds_npc_race,
test_npc_furrification) is the regression check for downstream callers.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from furrifier.context import FurryContext


def _stub_clfm(form_id: int, editor_id: str, cnam_rgba: tuple[int, int, int, int]):
    """Minimal CLFM record stub: form_id, editor_id, get_subrecord('CNAM')."""
    rec = MagicMock()
    rec.signature = 'CLFM'
    rec.editor_id = editor_id
    rec.form_id = MagicMock()
    rec.form_id.value = form_id
    # normalize_form_id: passthrough — assume the test passes already-
    # normalized FormIDs (the index is keyed by what normalize returns).
    rec.normalize_form_id.side_effect = lambda fid: type(
        'A', (), {'value': form_id})()
    cnam = MagicMock()
    cnam.size = 4
    cnam.data = bytes(cnam_rgba)
    rec.get_subrecord.side_effect = (
        lambda sig: cnam if sig == 'CNAM' else None)
    return rec


def _stub_plugin(records: list):
    """Plugin stub: get_records_by_signature(...) returns matching list."""
    plugin = MagicMock()
    plugin.get_records_by_signature.side_effect = (
        lambda sig: [r for r in records if r.signature == sig])
    return plugin


def _stub_furry_context(plugin_set):
    """FurryContext with just enough wiring for the resolver tests.
    Bypasses the heavy __init__ — we set the attribute directly."""
    ctx = FurryContext.__new__(FurryContext)
    ctx.plugin_set = plugin_set
    return ctx


class TestClfmFormIdIndex:
    def test_index_keeps_low24_collisions_distinct(self):
        """Two CLFMs at the same low-24 but different masters must
        index under different keys — the collision case the pre-
        Phase-4 resolver got wrong."""
        cellan = _stub_clfm(0x050012DD, 'CellanFur06GrayBrown', (75, 60, 50, 0))
        ece    = _stub_clfm(0x0E0012DD, 'Red01',                (200, 30, 30, 0))
        plugin = _stub_plugin([cellan, ece])
        plugin_set = [plugin]
        ctx = _stub_furry_context(plugin_set)

        index = ctx._build_clfm_form_id_index()
        assert 0x050012DD in index
        assert 0x0E0012DD in index
        assert index[0x050012DD] is cellan
        assert index[0x0E0012DD] is ece

    def test_resolve_color_uses_full_form_id(self):
        """_resolve_color must consult the FormID index, not low-24
        wildcard. Same-low-24 collisions must resolve to the correct
        CNAM color."""
        cellan = _stub_clfm(0x050012DD, 'CellanFur06GrayBrown', (75, 60, 50, 0))
        ece    = _stub_clfm(0x0E0012DD, 'Red01',                (200, 30, 30, 0))
        plugin_set = [_stub_plugin([cellan, ece])]
        ctx = _stub_furry_context(plugin_set)

        assert ctx._resolve_color(0x050012DD) == (75, 60, 50, 0)
        assert ctx._resolve_color(0x0E0012DD) == (200, 30, 30, 0)

    def test_resolve_color_unknown_returns_white_default(self):
        plugin_set = [_stub_plugin([])]
        ctx = _stub_furry_context(plugin_set)
        # Default fallback when not in index.
        assert ctx._resolve_color(0x0BADBEEF) == (255, 255, 255, 0)


class TestClfmEditorIdIndex:
    def test_load_order_winner_wins_on_edid_collision(self):
        """EnhancedCharacterEdit.esp overrides Skyrim.esm's
        HairColor15SteelGrey. Plugins iterate in load order, so the
        last (winning override) lands in the index."""
        skyrim = _stub_clfm(0x000A0436, 'HairColor15SteelGrey', (100, 100, 100, 0))
        ece    = _stub_clfm(0x000A0436, 'HairColor15SteelGrey', (110, 110, 110, 0))
        # Skyrim loads first, ECE loads later (winning override).
        plugin_set = [_stub_plugin([skyrim]), _stub_plugin([ece])]
        ctx = _stub_furry_context(plugin_set)

        index = ctx._build_clfm_edid_index()
        assert index['HairColor15SteelGrey'] is ece

    def test_resolve_color_by_edid_returns_cnam(self):
        clfm = _stub_clfm(0x05000918, 'BDMinoCoatBlack', (20, 20, 20, 0))
        plugin_set = [_stub_plugin([clfm])]
        ctx = _stub_furry_context(plugin_set)
        assert ctx._resolve_color_by_edid('BDMinoCoatBlack') == (20, 20, 20, 0)

    def test_resolve_color_by_edid_unknown_returns_none(self):
        plugin_set = [_stub_plugin([])]
        ctx = _stub_furry_context(plugin_set)
        assert ctx._resolve_color_by_edid('NoSuchColor') is None
