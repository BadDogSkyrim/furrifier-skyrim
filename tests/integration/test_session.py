"""Tests for furrifier.session — the setup/per-NPC/bake primitives the
live preview pipeline (Phase 3) stands on.

The full Run path already exercises this code path via
`run_furrification`, which is covered by the existing integration
suite. These tests specifically verify the *incremental* path:
setup_session can be driven one NPC at a time without re-doing the
per-session work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from furrifier.config import FurrifierConfig
from furrifier.session import FurrificationSession, setup_session


GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    """A fully-set-up session ready for per-NPC furrification.

    Built against the live Skyrim install + the default all_races
    scheme — this is an integration test, skipped when the game data
    isn't available.
    """
    if not (GAME_DATA / "Skyrim.esm").exists():
        pytest.skip("Skyrim.esm not available")
    out_dir = tmp_path_factory.mktemp("session_out")
    config = FurrifierConfig(
        patch_filename="TestSessionPatch.esp",
        race_scheme="all_races",
        game_data_dir=str(GAME_DATA),
        output_dir=str(out_dir),
        build_facegen=False,
    )
    return setup_session(config)


def test_setup_session_returns_session_dataclass(session):
    """The dataclass holds everything downstream consumers need."""
    assert isinstance(session, FurrificationSession)
    assert session.config.race_scheme == "all_races"
    assert session.plugin_set is not None
    assert session.patch is not None
    assert session.context is not None


def test_setup_session_applied_race_level_furrification(session):
    """Races / headpart FormLists / race presets get furrified during
    setup so they're visible to per-NPC work that follows. The simplest
    signal: the patch already has *some* overridden records before any
    NPC iteration runs."""
    patch = session.patch
    # Race overrides land as RACE records in the patch.
    race_overrides = list(patch.get_records_by_signature("RACE"))
    assert len(race_overrides) > 0, (
        "setup_session should have furrified at least one race into "
        "the patch, but RACE record list is empty"
    )


def test_furrify_npc_on_demand_produces_override(session):
    """Driving a single NPC through `context.furrify_npc` is the
    workhorse of the preview pipeline. Verify it produces a record
    that has the expected shape without needing to iterate the full
    load order."""
    # Dervenin (0x0001327C) is a stable Wood Elf NPC.
    DERVENIN = 0x0001327C
    chain = session.plugin_set.get_override_chain(DERVENIN)
    assert chain, "couldn't resolve Dervenin — unexpected on vanilla Skyrim"
    npc = chain[-1]

    result = session.context.furrify_npc(npc)
    assert result is not None, "scheme should furrify Wood Elf NPCs"
    assert result.signature == "NPC_"
    # The override must have an RNAM (race reference). Furrification
    # rewrites it to point at the furry race.
    rnam = result.get_subrecord("RNAM")
    assert rnam is not None


def test_bake_facegen_for_writes_nif(session, tmp_path):
    """bake_facegen_for is the single-NPC facegen entry point the live
    preview pipeline will call. Verify it writes a non-empty nif."""
    from furrifier.session import bake_facegen_for

    DERVENIN = 0x0001327C
    npc = session.plugin_set.get_override_chain(DERVENIN)[-1]
    patched = session.context.furrify_npc(npc)
    assert patched is not None

    nif_path, dds_path = bake_facegen_for(
        patched, session, out_dir=tmp_path)

    assert nif_path.is_file()
    assert nif_path.stat().st_size > 1000, (
        f"nif suspiciously small: {nif_path.stat().st_size} bytes — "
        "likely no shapes resolved"
    )
    # Dervenin has tint layers, so we should also have a DDS.
    assert dds_path is not None
    assert dds_path.is_file()
