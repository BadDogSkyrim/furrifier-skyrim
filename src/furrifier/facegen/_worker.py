"""Worker process for parallel facegen baking.

The parent process does the cheap serial work (form-id resolution via
`extract_npc_info`) and ships a list of `_WorkItem`s to a
`ProcessPoolExecutor`. Each worker holds one long-lived `AssetResolver`
for its lifetime — its image_cache and BSA-extract cache amortise across
NPCs the same way the single-process serial path does.

Workers do NOT touch `plugin_set`. Everything they need is already
inside the `info` dict — paths, morph values, tints — so they don't
need to be pickled the plugin set (which holds open file handles and
isn't cleanly pickleable anyway).
"""
from __future__ import annotations

import atexit
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .assemble import build_facegen_nif
from .assets import AssetResolver
from .composite import build_facetint_dds


log = logging.getLogger("furrifier.facegen.worker")


@dataclass
class _WorkItem:
    """One NPC's complete facegen job, fully serializable.

    `info` is the dict returned by `extract_npc_info`; it carries every
    resolved headpart path, morph value, and tint layer the builder
    needs. `nif_path` and `tint_dir` are the output paths (absolute).
    `tint_dir` is None when the NPC has no race-tint layers — workers
    then skip the DDS bake entirely."""
    edid: str
    info: Dict[str, Any]
    nif_path: Path
    tint_dir: Optional[Path]
    facetint_size: Optional[int]


@dataclass
class _Result:
    """One NPC's outcome. Phase timings come back so the parent can
    accumulate the same end-of-run averages the serial path printed."""
    edid: str
    ok: bool
    dds_written: int
    error: Optional[str]
    t_nif: float
    t_tint: float


# Module globals set by `_worker_init` and read by `_bake_one`. Each
# worker process has its own copy thanks to multiprocessing's spawn
# isolation, so this looks like a singleton even though every worker
# has its own.
_resolver: Optional[AssetResolver] = None


def _set_below_normal_priority() -> None:
    """Demote this process to BELOW_NORMAL on Windows so the user's
    foreground apps preempt freely. No-op on non-Windows (the kit only
    ships for Windows, but tests run on the dev box and shouldn't fail
    on a hypothetical Linux CI)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
        handle = kernel32.GetCurrentProcess()
        kernel32.SetPriorityClass(handle, BELOW_NORMAL_PRIORITY_CLASS)
    except Exception as exc:
        # Priority is a nice-to-have; failure here shouldn't stop a bake.
        log.debug("could not set BELOW_NORMAL priority: %s", exc)


def _worker_init(data_dir_str: str, throttle: bool) -> None:
    """Per-worker initializer: opens BSAs once and stashes the resolver.

    Called by `ProcessPoolExecutor(initializer=...)` exactly once per
    worker process. The resolver is closed at worker exit via atexit
    so the temp BSA-extract dir doesn't leak."""
    global _resolver
    if throttle:
        _set_below_normal_priority()
    _resolver = AssetResolver.for_data_dir(Path(data_dir_str))
    atexit.register(_close_resolver)


def _close_resolver() -> None:
    global _resolver
    if _resolver is not None:
        try:
            _resolver.close()
        except Exception:
            pass
        _resolver = None


def _bake_one(item: _WorkItem) -> _Result:
    """Build one NPC's facegen nif + (optional) tint DDS.

    Exceptions inside the build path are captured and returned in the
    `_Result` rather than propagated — the parent loop is what decides
    what counts as a per-NPC failure vs a real error, and re-raising
    here would abort the whole pool."""
    if _resolver is None:
        # Safety net for direct calls in tests; production goes through
        # _worker_init. We don't know data_dir here, so just return an
        # error — tests that call _bake_one directly should set up the
        # resolver themselves via _install_resolver_for_testing.
        return _Result(item.edid, False, 0, "resolver not initialized", 0.0, 0.0)
    try:
        t0 = time.perf_counter()
        build_facegen_nif(item.info, _resolver, item.nif_path)
        t1 = time.perf_counter()
        dds_written = 0
        if item.tint_dir is not None:
            build_facetint_dds(item.info, _resolver, item.tint_dir,
                               output_size=item.facetint_size)
            dds_written = 1
        t2 = time.perf_counter()
        return _Result(item.edid, True, dds_written, None,
                       t_nif=t1 - t0, t_tint=t2 - t1)
    except Exception as exc:
        return _Result(item.edid, False, 0, str(exc), 0.0, 0.0)


def _install_resolver_for_testing(resolver: AssetResolver) -> None:
    """Install a pre-built resolver so in-process tests can call
    `_bake_one` without going through the pool. Tests should call
    `_close_resolver()` (or just let atexit do it) when done."""
    global _resolver
    _resolver = resolver


def _pick_worker_count(throttle: bool, env_override: Optional[str] = None) -> int:
    """Workers to spawn.

    Resolution order: explicit env var (FURRIFY_FACEGEN_WORKERS) →
    throttle=1 → cpu_count-1 capped at 8.

    The cap at 8 reflects measured falloff: past that the marginal
    gain flattens (memory pressure, BSA contention) on consumer
    boxes. Subtracting 1 leaves a core for the main process / OS so
    progress UI stays responsive."""
    env_val = env_override if env_override is not None else os.environ.get(
        "FURRIFY_FACEGEN_WORKERS")
    if env_val:
        try:
            n = int(env_val)
            if n >= 1:
                return min(n, os.cpu_count() or n)
        except ValueError:
            log.warning("FURRIFY_FACEGEN_WORKERS=%r is not an int; ignoring",
                        env_val)
    if throttle:
        return 1
    return max(1, min(8, (os.cpu_count() or 4) - 1))
