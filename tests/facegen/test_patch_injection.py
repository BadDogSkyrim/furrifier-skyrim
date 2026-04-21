"""Tests for the patch-into-plugin_set injection used by
`build_facegen_for_patch`.

The furrifier excludes its own patch from the plugin_set's load order
at load time (the patch often doesn't exist yet, and a stale copy
would contaminate the master chain). Once furrification is done and
the patch is saved, facegen extraction needs to see the patch's
overrides — otherwise an NPC whose race was furrified in the patch
(patched NordRace → furry head defaults) will still resolve to the
vanilla NordRace when facegen looks up the race record, and the
builder hands back vanilla human headparts.

These tests bolt a synthetic override onto a real Skyrim.esm load
and assert injection makes it visible via `resolve_form_id`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from esplib import LoadOrder, PluginSet, Plugin

from furrifier.facegen import _inject_patch_into_plugin_set


GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")


@pytest.fixture(scope="module")
def skyrim_plugin_set():
    if not (GAME_DATA / "Skyrim.esm").exists():
        pytest.skip("Skyrim.esm not available")
    lo = LoadOrder.from_list(
        ["Skyrim.esm"], data_dir=str(GAME_DATA), game_id="tes5")
    ps = PluginSet(lo)
    ps.load_all()
    return ps


def _make_patch_overriding(npc_form_id: int,
                           plugin_set: PluginSet,
                           tmp_path: Path) -> Plugin:
    """Build an in-memory patch plugin that overrides one NPC record,
    mirroring the shape the furrifier produces. The override's content
    doesn't matter for this test — we just need SOMETHING resolvable
    through the patch so `resolve_form_id` has a record to return."""
    chain = plugin_set.get_override_chain(npc_form_id)
    original = chain[-1]

    patch = Plugin.new_plugin(tmp_path / "TestPatch.esp")
    patch.plugin_set = plugin_set

    # Make sure the patch's master list can resolve the formid's
    # high byte (Skyrim.esm index 0) — must happen before add_record
    # so Record.form_id.file_index points correctly.
    patch.add_master("Skyrim.esm")

    from esplib.record import Record, SubRecord
    override = Record(original.signature, original.form_id.value, 0)
    for sr in original.subrecords:
        override.subrecords.append(SubRecord(sr.signature, bytes(sr.data)))
    patch.add_record(override)
    return patch


def test_inject_makes_override_visible(skyrim_plugin_set, tmp_path):
    """Without injection: resolve returns vanilla. After injection:
    resolve sees the patch's override. This is the load-bearing
    behavior for facegen correctness."""
    # Pick any NPC — Dervenin works because he's present in Skyrim.esm.
    DERVENIN = 0x0001327C

    # Baseline: vanilla record wins the override chain.
    vanilla_chain = skyrim_plugin_set.get_override_chain(DERVENIN)
    assert len(vanilla_chain) == 1
    vanilla_rec = vanilla_chain[-1]
    assert vanilla_rec.plugin.file_path.name == "Skyrim.esm"

    patch = _make_patch_overriding(DERVENIN, skyrim_plugin_set, tmp_path)
    _inject_patch_into_plugin_set(skyrim_plugin_set, patch)

    # After injection the patch's override is the winning record.
    chain_after = skyrim_plugin_set.get_override_chain(DERVENIN)
    assert len(chain_after) == 2, (
        f"expected patch on top of vanilla, got {len(chain_after)}-deep chain"
    )
    assert chain_after[-1].plugin is patch, (
        f"patch's override did not win the chain after injection"
    )


def test_inject_is_idempotent(skyrim_plugin_set, tmp_path):
    """Calling inject twice with the same patch must not duplicate
    entries or corrupt the load order."""
    patch = _make_patch_overriding(0x0001327C, skyrim_plugin_set, tmp_path)
    before_count = skyrim_plugin_set.load_order.plugins.count("TestPatch.esp")

    _inject_patch_into_plugin_set(skyrim_plugin_set, patch)
    _inject_patch_into_plugin_set(skyrim_plugin_set, patch)

    after_count = skyrim_plugin_set.load_order.plugins.count("TestPatch.esp")
    # Either 0→1 (fresh) or 1→1 (already there from the first test); in
    # both cases the increment across two injects must be at most 1.
    assert after_count - before_count <= 1
