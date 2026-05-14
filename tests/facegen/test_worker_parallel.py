"""Byte-equivalence test for the parallel facegen path.

The worker process must produce byte-identical output to the in-process
serial path — anything else is a regression introduced by the pool
machinery (pickle drift, worker-init divergence, resolver state leak).

Skips cleanly without Skyrim.esm or the vanilla-asset snapshot. Same
gating as `test_live_pipeline.py`.
"""
from __future__ import annotations

import hashlib
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from esplib import LoadOrder, PluginSet


GAME_DATA = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition\Data")
VANILLA_ASSETS = Path(r"C:\Modding\SkyrimSEAssets\00 Vanilla Assets")

DERVENIN = 0x0001327C


def _ensure_pynifly():
    p = r"C:\Modding\PyNifly\io_scene_nifly"
    if p not in sys.path:
        sys.path.insert(0, p)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def dervenin_workitem(tmp_path_factory):
    """One real `_WorkItem` derived from a vanilla NPC, suitable for
    feeding either the in-process or pool-driven bake path."""
    if not (GAME_DATA / "Skyrim.esm").exists():
        pytest.skip("Skyrim.esm not available")
    if not VANILLA_ASSETS.is_dir():
        pytest.skip("vanilla assets snapshot not available")

    _ensure_pynifly()
    from furrifier.facegen import extract_npc_info
    from furrifier.facegen._worker import _WorkItem

    load_order = LoadOrder.from_list(
        ["Skyrim.esm"], data_dir=str(GAME_DATA), game_id="tes5")
    ps = PluginSet(load_order)
    ps.load_all()
    chain = ps.get_override_chain(DERVENIN)
    npc = chain[-1]
    info = extract_npc_info(npc, ps, patch_plugin_name="Skyrim.esm")
    return info


def _make_workitem(info: dict, out_dir: Path):
    from furrifier.facegen._worker import _WorkItem
    nif_path = out_dir / f"{info['form_id']}.nif"
    return _WorkItem(
        edid=info.get("npc_edid") or info["form_id"],
        info=info,
        nif_path=nif_path,
        tint_dir=out_dir if info.get("tints") else None,
        facetint_size=None,
    )


def test_parallel_bake_matches_serial(dervenin_workitem, tmp_path):
    """Bake one NPC twice — once via in-process `_bake_one` with a
    locally-installed resolver, once via a real `ProcessPoolExecutor`
    spinning up its own worker. The output bytes must be byte-identical.

    Catches every Phase 1 regression class: WorkItem pickling, worker
    init differing from in-process init, resolver state leaking across
    processes."""
    from furrifier.facegen.assets import AssetResolver
    from furrifier.facegen._worker import (
        _bake_one, _close_resolver, _install_resolver_for_testing,
        _worker_init,
    )

    info = dervenin_workitem

    # --- Serial path: in-process bake with a locally-installed resolver.
    serial_out = tmp_path / "serial"
    serial_out.mkdir()
    serial_item = _make_workitem(info, serial_out)
    resolver = AssetResolver(VANILLA_ASSETS, bsa_readers=[])
    try:
        _install_resolver_for_testing(resolver)
        serial_result = _bake_one(serial_item)
    finally:
        _close_resolver()  # also clears the test-installed resolver
    assert serial_result.ok, serial_result.error

    # --- Parallel path: spin up a real worker process and run the same
    # WorkItem through it. Output goes into a separate dir so we can
    # diff bytes.
    parallel_out = tmp_path / "parallel"
    parallel_out.mkdir()
    parallel_item = _make_workitem(info, parallel_out)
    with ProcessPoolExecutor(
            max_workers=1,
            initializer=_worker_init,
            initargs=(str(VANILLA_ASSETS), False)) as pool:
        parallel_result = list(pool.map(_bake_one, [parallel_item]))[0]
    assert parallel_result.ok, parallel_result.error

    # --- Byte-equivalence: every file under serial_out must match the
    # same-named file under parallel_out, byte-for-byte.
    serial_files = sorted(p.relative_to(serial_out)
                          for p in serial_out.rglob("*") if p.is_file())
    parallel_files = sorted(p.relative_to(parallel_out)
                            for p in parallel_out.rglob("*") if p.is_file())
    assert serial_files == parallel_files, (
        f"file set differs: serial={serial_files} parallel={parallel_files}")
    for rel in serial_files:
        h_serial = _hash(serial_out / rel)
        h_parallel = _hash(parallel_out / rel)
        assert h_serial == h_parallel, (
            f"{rel} differs: serial={h_serial[:12]} parallel={h_parallel[:12]}")


def test_pick_worker_count_throttle_caps_at_one():
    from furrifier.facegen._worker import _pick_worker_count
    assert _pick_worker_count(throttle=True) == 1


def test_pick_worker_count_env_override():
    from furrifier.facegen._worker import _pick_worker_count
    # Env override wins even when throttle would otherwise force 1.
    assert _pick_worker_count(throttle=True, env_override="3") == 3
    assert _pick_worker_count(throttle=False, env_override="3") == 3


def test_pick_worker_count_default_leaves_one_core():
    import os
    from furrifier.facegen._worker import _pick_worker_count
    n = _pick_worker_count(throttle=False)
    cpu = os.cpu_count() or 4
    assert 1 <= n <= 8
    if cpu > 1:
        assert n <= cpu - 1


def test_workitem_pickleable(dervenin_workitem, tmp_path):
    """The info dict ships across the process boundary as part of
    WorkItem. If it carries a non-pickleable object (Plugin handle,
    Record back-reference), `pool.map` would fail with PicklingError —
    catch that here, in-process, instead of at runtime."""
    import pickle
    from furrifier.facegen._worker import _WorkItem
    item = _make_workitem(dervenin_workitem, tmp_path)
    blob = pickle.dumps(item)
    restored = pickle.loads(blob)
    assert restored.edid == item.edid
    assert restored.info["form_id"] == item.info["form_id"]
    assert restored.nif_path == item.nif_path
