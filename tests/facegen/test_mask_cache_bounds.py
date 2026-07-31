"""Tests for the bounded mask cache.

Unbounded, this cache grew one entry per (mask, canvas size) pair for
the life of a worker process. At a 2048px canvas that was tens of MiB
apiece; eight workers between them exhausted a 64 GiB machine mid-run,
and every NPC whose composite landed while memory was tight got skipped
with no facegen written at all.
"""

import numpy as np
import pytest

from furrifier.facegen.assets import BoundedArrayCache


def _arr(mib: int) -> np.ndarray:
    return np.zeros(mib * 1024 * 1024, dtype=np.uint8)


def test_evicts_to_stay_under_budget():
    cache = BoundedArrayCache(max_bytes=10 * 1024 * 1024)
    for i in range(8):
        cache[f"k{i}"] = _arr(2)
    assert cache.nbytes <= cache.max_bytes
    assert cache.evictions > 0


def test_evicts_least_recently_used():
    cache = BoundedArrayCache(max_bytes=3 * 1024 * 1024)
    cache["a"] = _arr(1)
    cache["b"] = _arr(1)
    cache["c"] = _arr(1)

    cache.get("a")          # 'a' is now the most recent, 'b' the oldest
    cache["d"] = _arr(1)    # forces one eviction

    assert cache.get("b") is None, "LRU entry should have been evicted"
    assert cache.get("a") is not None
    assert cache.get("d") is not None


def test_hit_returns_the_same_object():
    cache = BoundedArrayCache(max_bytes=4 * 1024 * 1024)
    arr = _arr(1)
    cache["k"] = arr
    assert cache.get("k") is arr


def test_miss_returns_default():
    cache = BoundedArrayCache(max_bytes=1024)
    assert cache.get("nope") is None
    assert cache.get("nope", "fallback") == "fallback"


def test_oversized_entry_is_not_cached():
    """An array bigger than the whole budget can never pay off, and
    admitting it would evict everything else first."""
    cache = BoundedArrayCache(max_bytes=1 * 1024 * 1024)
    cache["small"] = _arr(1) [:512 * 1024]
    cache["huge"] = _arr(4)

    assert cache.get("huge") is None
    assert cache.get("small") is not None
    assert cache.nbytes <= cache.max_bytes


def test_overwrite_does_not_double_count():
    cache = BoundedArrayCache(max_bytes=8 * 1024 * 1024)
    cache["k"] = _arr(1)
    first = cache.nbytes
    cache["k"] = _arr(1)
    assert cache.nbytes == first
    assert len(cache) == 1


def test_budget_from_env(monkeypatch):
    monkeypatch.setenv("FURRIFY_MASK_CACHE_MB", "7")
    assert BoundedArrayCache().max_bytes == 7 * 1024 * 1024


@pytest.mark.parametrize("bad", ["0", "-3", "banana"])
def test_bad_env_falls_back_to_default(monkeypatch, bad):
    monkeypatch.setenv("FURRIFY_MASK_CACHE_MB", bad)
    assert BoundedArrayCache().max_bytes == 256 * 1024 * 1024
