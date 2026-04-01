"""Shared fixtures for furrifier integration tests.

These tests require real game files (Skyrim.esm, YAS race mods, etc.)
and are skipped if the files aren't found.

All tests share a single patch plugin (FurrifierTEST.esp) saved to the
game Data directory so results can be inspected in xEdit.

Two-phase testing:
  Tests that modify records use the furrify_and_check fixture. Each test
  provides a write callback (runs immediately) and a verify callback
  (deferred until after save/reload). The final test_verify_saved_plugin
  test saves the patch, reopens it, and runs all deferred verify callbacks.
"""

import pytest
from pathlib import Path

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin, LoadOrder, PluginSet, find_game_data, find_strings_dir

from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla


def find_skyrim_data() -> Path | None:
    return find_game_data('tes5')


PATCH_FILENAME = "FurrifierTEST.esp"

PLUGIN_NAMES = [
    "Skyrim.esm",
    "Update.esm",
    "Dawnguard.esm",
    "HearthFires.esm",
    "Dragonborn.esm",
    "BDCatRaces.esp",
    "YASCanineRaces.esp",
]

requires_gamefiles = pytest.mark.skipif(
    find_skyrim_data() is None,
    reason="Skyrim data files not found",
)


# -- Registry for deferred verify callbacks --

_verify_registry: list[tuple[str, callable]] = []


def get_verify_registry():
    return _verify_registry


def clear_verify_registry():
    _verify_registry.clear()


# -- Fixtures --


@pytest.fixture(scope="session")
def data_dir():
    d = find_skyrim_data()
    if d is None:
        pytest.skip("Skyrim data files not found")
    return d


@pytest.fixture(scope="session")
def ctx():
    """Fully configured RaceDefContext with all_races scheme + vanilla setup."""
    c = load_scheme('all_races')
    setup_vanilla(c)
    return c


@pytest.fixture(scope="session")
def plugin_set(data_dir):
    """All relevant plugins loaded via PluginSet."""
    lo = LoadOrder.from_list(PLUGIN_NAMES, data_dir=data_dir, game_id='tes5')
    ps = PluginSet(lo)
    strings_dir = find_strings_dir()
    if strings_dir:
        ps.string_search_dirs = [str(strings_dir)]
    ps.load_all()
    return ps


@pytest.fixture(scope="session")
def all_plugins(plugin_set):
    """All loaded plugins as a list (for backward compatibility)."""
    return list(plugin_set)


@pytest.fixture(scope="session")
def races_by_edid(plugin_set, ctx):
    """Race records indexed by EditorID, with assignments linked."""
    races = {}
    needed = set()
    for a in ctx.assignments.values():
        needed.add(a.vanilla_id)
        needed.add(a.furry_id)
    for s in ctx.subraces.values():
        needed.add(s.vanilla_basis)
        needed.add(s.furry_id)

    for plugin in plugin_set:
        for record in plugin.get_records_by_signature('RACE'):
            edid = record.editor_id
            if edid and edid in needed:
                races[edid] = record  # last wins = winning override

    # Link assignments
    for a in ctx.assignments.values():
        a.vanilla = _to_race_info(races.get(a.vanilla_id))
        a.furry = _to_race_info(races.get(a.furry_id))

    return races


def _to_race_info(record):
    """Convert a Record to a minimal RaceInfo-like object, or None."""
    if record is None:
        return None
    from furrifier.models import RaceInfo
    from furrifier.furry_load import is_child_race
    return RaceInfo(record=record, editor_id=record.editor_id,
                    is_child=is_child_race(record))


@pytest.fixture(scope="session")
def all_headparts(plugin_set, ctx):
    """All headpart records indexed by EditorID."""
    from furrifier.furry_load import get_headpart_type
    from furrifier.models import HeadpartInfo
    headparts = {}
    for plugin in plugin_set:
        for record in plugin.get_records_by_signature('HDPT'):
            edid = record.editor_id
            if edid is None:
                continue
            hp_type = get_headpart_type(record)
            labels = ctx.headpart_labels.get(edid, [])
            equivalents = ctx.headpart_equivalents.get(edid, [])
            headparts[edid] = HeadpartInfo(
                record=record, editor_id=edid, hp_type=hp_type,
                labels=list(labels), equivalents=list(equivalents),
            )
    return headparts


@pytest.fixture(scope="session")
def patch(request, plugin_set, data_dir):
    """Shared patch plugin written to the game Data directory.

    All tests accumulate records into this single plugin. It is also
    saved to FurrifierTEST.esp at session end for xEdit inspection.
    """
    patch_path = data_dir / PATCH_FILENAME
    masters = [p.file_path.name for p in plugin_set if p.file_path]
    p = Plugin.new_plugin(patch_path, masters=masters[:254])


    def save_patch():
        p.save()
        print(f"\nSaved test plugin: {patch_path}")

    request.addfinalizer(save_patch)
    return p


@pytest.fixture(scope="session")
def race_headparts(plugin_set, all_headparts):
    """Index of headparts per (type, sex, race)."""
    from furrifier.furry_load import build_race_headparts
    return build_race_headparts(list(plugin_set), all_headparts)


@pytest.fixture(scope="session")
def race_tints(plugin_set):
    """Tint data per (race, sex)."""
    from furrifier.furry_load import build_race_tints
    return build_race_tints(list(plugin_set))


@pytest.fixture(scope="session")
def furry_ctx(patch, ctx, races_by_edid, all_headparts, race_headparts,
              race_tints, plugin_set):
    """FurryContext wired up for testing."""
    from furrifier.context import FurryContext
    fc = FurryContext(
        patch=patch,
        ctx=ctx,
        races=races_by_edid,
        all_headparts=all_headparts,
        race_headparts=race_headparts,
        race_tints=race_tints,
        plugin_set=plugin_set,
    )
    fc.furrify_all_races()
    return fc


@pytest.fixture
def furrify_and_check(request, furry_ctx):
    """Two-phase test fixture: write now, verify after save/reload.

    Usage:
        def test_something(furrify_and_check, all_plugins):
            npc, _ = find_record(all_plugins, 'NPC_', 'SomeNPC')

            def write(furry_ctx):
                furry_ctx.furrify_npc(npc)

            def verify(reloaded):
                patched = find_by_formid(reloaded, npc.form_id)
                assert patched is not None

            furrify_and_check(write, verify)
    """

    def register(write_fn, verify_fn):
        write_fn(furry_ctx)
        # Don't register verify for xfail tests -- those failures are
        # expected and would pollute test_verify_saved_plugin.
        marker = request.node.get_closest_marker('xfail')
        if marker is None:
            test_name = request.node.name
            _verify_registry.append((test_name, verify_fn))

    return register


# -- Helpers --


def find_record(plugins, signature, editor_id):
    """Find a record by signature and EditorID across plugins."""
    for plugin in plugins:
        for record in plugin.get_records_by_signature(signature):
            if record.editor_id == editor_id:
                return record, plugin
    return None, None


def run_verify_phase(patch):
    """Save the patch to a temp file, reload it, run all verify callbacks.

    Returns a list of failure messages, or empty list if all passed.
    Always saves to a temp file to avoid clobbering the shared patch path.
    """
    import tempfile

    registry = get_verify_registry()
    if not registry:
        return []

    # Save to temp — don't touch the shared patch.file_path
    save_path = Path(tempfile.mkdtemp()) / PATCH_FILENAME
    original_path = patch.file_path
    patch.file_path = save_path
    patch.save()
    patch.file_path = original_path

    reloaded = Plugin()
    reloaded.load(save_path)

    failures = []
    for test_name, verify_fn in registry:
        try:
            verify_fn(reloaded)
        except Exception as e:
            failures.append(f"[{test_name}] {e}")

    clear_verify_registry()
    return failures


def find_by_formid(plugin, form_id):
    """Find a record by FormID in a plugin."""
    fid = form_id.value if hasattr(form_id, 'value') else form_id
    for record in plugin.records:
        if record.form_id.value == fid:
            return record
    return None
