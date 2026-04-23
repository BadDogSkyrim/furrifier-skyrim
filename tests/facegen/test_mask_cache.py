"""Tests for the compositor mask cache.

For a run with thousands of NPCs across a handful of races, the same
tint masks are loaded hundreds of times. A simple run-scoped cache
eliminates the redundant decodes — Pillow's DDS decoder is pure-Python
and dominates the facegen runtime.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from furrifier.facegen.assets import AssetResolver
from furrifier.facegen.composite import composite_layers


def _write_solid_mask(path: Path, color=(255, 255, 255, 255),
                      size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "Data"
    d.mkdir()
    _write_solid_mask(d / "textures/red.dds", color=(255, 0, 0, 255))
    _write_solid_mask(d / "textures/green.dds", color=(0, 255, 0, 255))
    return d


def test_cache_reduces_mask_loads_across_npcs(data_dir, monkeypatch):
    """When composite_layers is called twice for two NPCs that share
    the same mask paths, each unique mask must only be decoded once
    (via the cache on the resolver)."""
    from furrifier.facegen import composite as comp_mod
    load_calls: list[Path] = []
    original = comp_mod.load_mask_coverage

    def counting_load(path, target_size=None):
        load_calls.append(Path(path))
        return original(path, target_size)

    monkeypatch.setattr(comp_mod, "load_mask_coverage", counting_load)

    npc1_tints = [
        {"tini": 1, "color": [255, 0, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/red.dds"},
        {"tini": 2, "color": [0, 255, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/green.dds"},
    ]
    # Second NPC uses the same two masks.
    npc2_tints = list(npc1_tints)

    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        composite_layers(resolver, npc1_tints,
                         base_color=[0, 0, 0], output_size=64)
        mid = len(load_calls)
        composite_layers(resolver, npc2_tints,
                         base_color=[0, 0, 0], output_size=64)
        final = len(load_calls)

    # First NPC loads both masks (2 calls). Second NPC reuses both → 0
    # additional loads. Without the cache we'd see final == 4; with
    # it, final == mid == 2.
    assert mid == 2, f"first NPC should load 2 masks, got {mid}"
    assert final == 2, (
        f"second NPC should hit cache for both masks "
        f"(expected 2 total loads, got {final})"
    )


def test_no_double_decode_on_canvas_size_probe(data_dir, monkeypatch):
    """With output_size=None, the compositor picks the canvas size from
    the first resolvable mask's native shape. That probe must not force
    a full pixel decode — only a header read — so each unique mask is
    still decoded exactly once across the whole run."""
    from furrifier.facegen import composite as comp_mod
    load_calls: list[Path] = []
    original = comp_mod.load_mask_coverage

    def counting_load(path, target_size=None):
        load_calls.append(Path(path))
        return original(path, target_size)

    monkeypatch.setattr(comp_mod, "load_mask_coverage", counting_load)

    tints = [
        {"tini": 1, "color": [255, 0, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/red.dds"},
        {"tini": 2, "color": [0, 255, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/green.dds"},
    ]

    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        composite_layers(resolver, tints, base_color=[0, 0, 0])

    # Two unique masks → two decodes. Before the fix it was 3: red was
    # decoded once for the canvas-size probe at native, once for the
    # composite at the target size.
    assert len(load_calls) == 2, (
        f"expected one decode per mask, got {load_calls}"
    )


def test_cache_respects_target_size(data_dir, monkeypatch):
    """A mask loaded at size 256 and the same mask loaded at size 512
    must NOT share a cache entry — the Lanczos resample makes them
    different arrays. Otherwise we'd return a 256x256 array where a
    512x512 one was expected."""
    from furrifier.facegen import composite as comp_mod
    load_calls = []
    original = comp_mod.load_mask_coverage

    def counting_load(path, target_size=None):
        load_calls.append((str(path), target_size))
        return original(path, target_size)

    monkeypatch.setattr(comp_mod, "load_mask_coverage", counting_load)

    tints = [{"tini": 1, "color": [255, 0, 0, 255], "intensity": 1.0,
              "tias": -1, "tinp": 7, "mask": "textures/red.dds"}]

    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        composite_layers(resolver, tints, base_color=[0, 0, 0],
                         output_size=64)
        composite_layers(resolver, tints, base_color=[0, 0, 0],
                         output_size=128)

    # Two different sizes → two loads.
    sizes = sorted(set(sz for _, sz in load_calls))
    assert sizes == [64, 128], f"expected loads at both sizes, got {sizes}"
