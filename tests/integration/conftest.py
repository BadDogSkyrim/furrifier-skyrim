"""Shared fixtures for furrifier integration tests.

These tests require real game files (Skyrim.esm, YAS race mods, etc.)
and are skipped if the files aren't found.

All tests share a single patch plugin (FurrifierTEST.esp) saved to the
game Data directory so results can be inspected in xEdit.

The skyrim_plugin fixture comes from the root conftest (session-scoped,
loaded once for both esplib and furrifier tests).

Two-phase testing:
  Tests that modify records use the furrify_and_check fixture. Each test
  provides a write callback (runs immediately) and a verify callback
  (deferred until after save/reload). The final test_verify_saved_plugin
  test saves the patch, reopens it, and runs all deferred verify callbacks.
"""

import pytest
from pathlib import Path

import esplib.defs.tes5  # noqa: F401 -- registers tes5 game schemas
from esplib import Plugin

from furrifier.race_defs import load_scheme
from furrifier.vanilla_setup import setup_vanilla


SKYRIM_DATA_PATHS = [
    Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"),
    Path(r"C:\Program Files\Steam\steamapps\common\Skyrim Special Edition\Data"),
    Path(r"D:\Steam\steamapps\common\Skyrim Special Edition\Data"),
    Path(r"D:\SteamLibrary\steamapps\common\Skyrim Special Edition\Data"),
]

STRING_TABLE_PATHS = [
    Path(r"C:\Modding\SkyrimSEAssets\00 Vanilla Assets\strings"),
]


def find_skyrim_data() -> Path | None:
    for p in SKYRIM_DATA_PATHS:
        if p.exists():
            return p
    return None


def _find_strings_dir() -> Path | None:
    """Find directory containing Skyrim_english.STRINGS."""
    data = find_skyrim_data()
    if data:
        d = data / "Strings"
        if (d / "Skyrim_english.STRINGS").exists():
            return d
    for p in STRING_TABLE_PATHS:
        if p.exists():
            for f in p.iterdir():
                if f.name.lower() == "skyrim_english.strings":
                    return p
    return None


PATCH_FILENAME = "FurrifierTEST.esp"

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
def skyrim_plugin(data_dir):
    """Skyrim.esm loaded once per session.

    When running from the workspace root, the root conftest provides this
    fixture instead (shared across esplib and furrifier tests).
    """
    path = data_dir / "Skyrim.esm"
    if not path.exists():
        pytest.skip("Skyrim.esm not found")
    strings_dir = _find_strings_dir()
    p = Plugin()
    if strings_dir:
        p.string_search_dirs = [str(strings_dir)]
    p.load(path)
    return p


@pytest.fixture(scope="session")
def all_plugins(skyrim_plugin, data_dir):
    """All relevant plugins loaded. Reuses the session skyrim_plugin."""
    extra_names = [
        "Update.esm",
        "Dawnguard.esm",
        "HearthFires.esm",
        "Dragonborn.esm",
        "BDCatRaces.esp",
        "YASCanineRaces.esp",
    ]
    strings_dir = _find_strings_dir()
    search_dirs = [str(strings_dir)] if strings_dir else []
    plugins = [skyrim_plugin]
    for name in extra_names:
        path = data_dir / name
        if path.exists():
            p = Plugin()
            p.string_search_dirs = search_dirs
            p.load(path)
            plugins.append(p)
    return plugins


@pytest.fixture(scope="session")
def races_by_edid(all_plugins, ctx):
    """Race records indexed by EditorID, with assignments linked."""
    races = {}
    needed = set()
    for a in ctx.assignments.values():
        needed.add(a.vanilla_id)
        needed.add(a.furry_id)
    for s in ctx.subraces.values():
        needed.add(s.vanilla_basis)
        needed.add(s.furry_id)

    for plugin in all_plugins:
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
def all_headparts(all_plugins, ctx):
    """All headpart records indexed by EditorID."""
    from furrifier.furry_load import get_headpart_type
    from furrifier.models import HeadpartInfo
    headparts = {}
    for plugin in all_plugins:
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
def patch(request, all_plugins, data_dir):
    """Shared patch plugin written to the game Data directory.

    All tests accumulate records into this single plugin. It is also
    saved to FurrifierTEST.esp at session end for xEdit inspection.
    """
    patch_path = data_dir / PATCH_FILENAME
    masters = [p.file_path.name for p in all_plugins if p.file_path]
    p = Plugin.new_plugin(patch_path, masters=masters[:254])


    def save_patch():
        p.save()
        print(f"\nSaved test plugin: {patch_path}")

    request.addfinalizer(save_patch)
    return p


@pytest.fixture(scope="session")
def race_headparts(all_plugins, all_headparts):
    """Index of headparts per (type, sex, race)."""
    from furrifier.furry_load import build_race_headparts
    return build_race_headparts(all_plugins, all_headparts)


@pytest.fixture(scope="session")
def furry_ctx(patch, ctx, races_by_edid, all_headparts, race_headparts):
    """FurryContext wired up for testing."""
    from furrifier.context import FurryContext
    return FurryContext(
        patch=patch,
        ctx=ctx,
        races=races_by_edid,
        all_headparts=all_headparts,
        race_headparts=race_headparts,
        race_tints={},
    )


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
