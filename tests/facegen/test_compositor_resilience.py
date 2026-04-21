"""Resilience tests for the tint compositor: individual mask lookup
failures should warn-and-skip that layer, not abort the whole NPC.

Third-party mods sometimes reference tint masks that are shipped in
BSAs we can't open, or missing entirely. One missing layer out of five
shouldn't torpedo the NPC's face tint — the other four should still
composite cleanly.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from furrifier.facegen.assets import AssetResolver
from furrifier.facegen.composite import composite_layers, build_facetint_dds


def _write_solid_mask(path: Path, color=(255, 255, 255, 255),
                      size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "Data"
    d.mkdir()
    # One real mask and one missing relpath — the compositor should
    # produce output using only the real one.
    _write_solid_mask(d / "textures/m1.dds", color=(255, 255, 255, 255))
    return d


def test_composite_drops_missing_masks_with_warning(data_dir, caplog):
    """Two layers: one mask exists, one doesn't. Compositor should
    return the partial composite (not raise) and log a warning that
    names the missing path so the user can act on it."""
    tints = [
        {"tini": 10, "color": [255, 0, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/m1.dds"},
        {"tini": 11, "color": [0, 255, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/missing_layer.dds"},
    ]
    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        with caplog.at_level(logging.WARNING):
            acc = composite_layers(resolver, tints, base_color=[0, 0, 0],
                                   output_size=64)
    assert acc.shape == (64, 64, 4)
    # Any red present confirms the good layer was composited
    assert acc[..., 0].max() > 0.5
    # Must name the missing mask so the user knows what to look at
    joined = " ".join(r.message for r in caplog.records)
    assert "missing_layer.dds" in joined


def test_composite_survives_missing_skintone(data_dir, caplog):
    """If the SkinTone layer's mask can't be resolved, fall back to
    solid QNAM fill — same behavior as NPCs without a SkinTone TINP
    entry at all. Loss of texture detail is fine; hard failure is not."""
    tints = [
        # SkinTone layer whose mask is missing
        {"tini": 6, "color": [128, 128, 128, 255], "intensity": 1.0,
         "tias": -1, "tinp": 6, "mask": "textures/missing_skin.dds"},
        # Real layer (red) so output is visibly non-black
        {"tini": 10, "color": [255, 0, 0, 255], "intensity": 1.0, "tias": -1,
         "tinp": 7, "mask": "textures/m1.dds"},
    ]
    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        with caplog.at_level(logging.WARNING):
            acc = composite_layers(resolver, tints,
                                   base_color=[200, 150, 100],
                                   output_size=64)
    # Accumulator must still be produced; QNAM used as solid base.
    assert acc.shape == (64, 64, 4)
    # Red layer on top of skin-base fill shows red present.
    assert acc[..., 0].max() > 0.5
    joined = " ".join(r.message for r in caplog.records)
    assert "missing_skin.dds" in joined


def test_composite_all_layers_missing_warns_and_produces_flat_fill(data_dir, caplog):
    """If every referenced mask is missing, we still want *some* output
    rather than a hard crash — solid QNAM fill. The user's face tint
    won't have detail, but their NPC won't dark-face out."""
    tints = [
        {"tini": 10, "color": [255, 0, 0, 255], "intensity": 1.0,
         "tias": -1, "tinp": 7, "mask": "textures/missing1.dds"},
        {"tini": 11, "color": [0, 255, 0, 255], "intensity": 1.0,
         "tias": -1, "tinp": 7, "mask": "textures/missing2.dds"},
    ]
    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        with caplog.at_level(logging.WARNING):
            acc = composite_layers(resolver, tints, base_color=[200, 150, 100],
                                   output_size=64)
    assert acc.shape == (64, 64, 4)
    # QNAM base fills the canvas (R=200/255≈0.78)
    assert abs(acc[32, 32, 0] - 200 / 255.0) < 0.01


def test_build_facetint_dds_still_writes_when_some_masks_missing(data_dir):
    """End-to-end: a full build_facetint_dds call with a mix of real
    and missing masks should produce a valid DDS file."""
    tints = [
        {"tini": 10, "color": [255, 0, 0, 255], "intensity": 1.0,
         "tias": -1, "tinp": 7, "mask": "textures/m1.dds"},
        {"tini": 11, "color": [0, 255, 0, 255], "intensity": 1.0,
         "tias": -1, "tinp": 7, "mask": "textures/nope.dds"},
    ]
    npc_info = {
        "form_id": "000DEAD1", "tints": tints,
        "qnam_color": [100, 100, 100],
    }
    out_dir = data_dir / "facetint_out"
    with AssetResolver(data_dir, bsa_readers=[]) as resolver:
        png, dds = build_facetint_dds(npc_info, resolver, out_dir,
                                      output_size=256)
    assert png.is_file() and png.stat().st_size > 0
    assert dds.is_file() and dds.stat().st_size > 0
