"""`merge_armor_overrides` must group ARMOs by identity, not object index.

Grouping on `form_id & 0xFFFFFF` made unrelated armors from different
plugins overrides of each other, so the merge handed one armor another
armor's addons: 97 ARMO records in a shipped patch carried addons from
a different armor entirely (Stalhrim cuirasses with creature-skin
addons, a draugr body with ArmorHideHelmet's three addons).

See PLAN_FURRIFIER_FORMID_KEYS.md §3a.
"""
from __future__ import annotations

import pytest

from esplib import Plugin, PluginSet, Record
from esplib.load_order import LoadOrder
from esplib.utils import FormID

from furrifier.context import FurryContext
from furrifier.util import record_key


def _plugin(name: str, masters=None, is_esm: bool = False) -> Plugin:
    return Plugin.new_plugin(name, masters=masters, game='tes5',
                             is_esm=is_esm)


def _armo(plugin: Plugin, form_id: int, edid: str,
          addons: list[int]) -> Record:
    """ARMO in `plugin` at the raw `form_id`, listing `addons` as raw
    FormIDs -- both already in that plugin's master-list indexing.

    The file-index byte is what decides override vs new record: an
    OVERRIDE carries the defining master's index, while a plugin's own
    new record carries `len(masters)` (its self index). Getting that
    wrong makes an intended override a separate record and the merge
    correctly does nothing.
    """
    rec = Record('ARMO', FormID(form_id), 0)
    rec.add_subrecord('EDID', edid.encode() + b'\x00')
    for fid in addons:
        rec.add_subrecord('MODL', fid.to_bytes(4, 'little'))
    plugin.add_record(rec)
    return rec


def _plugin_set(*plugins: Plugin) -> PluginSet:
    lo = LoadOrder.from_list([p.file_path.name for p in plugins],
                             game_id='tes5')
    ps = PluginSet(lo)
    for p in plugins:
        ps._plugins[p.file_path.name] = p
        ps._loaded_full[p.file_path.name] = True
        p.plugin_set = ps
    ps.invalidate()
    return ps


@pytest.fixture
def sources():
    """Skyrim.esm plus two race mods that each add their own addon to its
    armor -- the case the merge exists for, since only ModB's override
    wins and it lacks ModA's addon. Plus an unrelated mod whose OWN armor
    happens to sit at the same object index; it is nobody's override."""
    base = _plugin('Skyrim.esm', is_esm=True)
    _armo(base, 0x00000800, 'SharedArmor', [0x00000900])

    # In each mod: master index 0 == Skyrim.esm, index 1 == the mod
    # itself. So 0x00000800 is an OVERRIDE of the vanilla armor, and
    # 0x0100090x is the mod's own new addon.
    mod_a = _plugin('ModA.esp', masters=['Skyrim.esm'])
    _armo(mod_a, 0x00000800, 'SharedArmor', [0x00000900, 0x01000901])

    mod_b = _plugin('ModB.esp', masters=['Skyrim.esm'])
    _armo(mod_b, 0x00000800, 'SharedArmor', [0x00000900, 0x01000902])

    # No masters, so 0x00000800 here is Other.esp's OWN armor -- a
    # different record that merely shares the object index.
    other = _plugin('Other.esp')
    _armo(other, 0x00000800, 'UnrelatedArmor', [0x00000903])

    return [base, mod_a, mod_b, other]


@pytest.fixture
def merged(sources):
    base, _mod_a, _mod_b, other = sources
    base_armo = next(iter(base.get_records_by_signature('ARMO')))
    other_armo = next(iter(other.get_records_by_signature('ARMO')))

    patch = _plugin('Patch.esp',
                    masters=['Skyrim.esm', 'ModA.esp', 'ModB.esp',
                             'Other.esp'])
    ps = _plugin_set(*sources, patch)

    furry = FurryContext(
        patch=patch, ctx=None, races={}, all_headparts={},
        race_headparts={}, race_tints={}, plugin_set=ps)
    furry.merge_armor_overrides(sources)

    by_key = {record_key(r): r
              for r in patch.get_records_by_signature('ARMO')}
    return by_key, base_armo, other_armo


def _addon_keys(rec: Record) -> set[int]:
    return {rec.normalize_form_id(sr.get_form_id()).value
            for sr in rec.get_subrecords('MODL') if sr.size >= 4}


class TestArmoGroupingByIdentity:

    def test_fixture_actually_collides_on_object_index(self, merged):
        _by_key, base_armo, other_armo = merged
        assert base_armo.form_id.value & 0xFFFFFF \
            == other_armo.form_id.value & 0xFFFFFF

    def test_real_overrides_still_merge(self, merged):
        """The mod's addon must reach the shared armor -- the feature
        this function exists for."""
        by_key, base_armo, _other = merged
        patched = by_key.get(record_key(base_armo))
        assert patched is not None, \
            "SharedArmor should be merged into the patch"
        assert len(_addon_keys(patched)) == 3, \
            "vanilla addon plus one from each race mod"

    def test_unrelated_armor_keeps_its_own_addons(self, merged):
        """UnrelatedArmor has exactly one override, so the merge has no
        business touching it at all."""
        by_key, _base, other_armo = merged
        patched = by_key.get(record_key(other_armo))
        assert patched is None, (
            "UnrelatedArmor has a single override and must not be "
            "merged; it only grouped with SharedArmor because they "
            "share object index 0x000800")

    def test_no_armo_gains_a_foreign_addon(self, merged, sources):
        """The shipped-patch invariant, stated generally: every addon on
        a patched ARMO must come from some real override of that same
        ARMO. This is the check that counted 97 violations on a live
        load order; it should count zero."""
        by_key, _base, _other = merged
        legit: dict[int, set[int]] = {}
        for plugin in sources:
            for rec in plugin.get_records_by_signature('ARMO'):
                legit.setdefault(record_key(rec), set()).update(
                    _addon_keys(rec))

        offenders = {
            key: sorted(_addon_keys(patched) - legit.get(key, set()))
            for key, patched in by_key.items()
            if _addon_keys(patched) - legit.get(key, set())
        }
        assert not offenders, \
            f"patched ARMOs carrying a foreign armor's addons: {offenders}"
