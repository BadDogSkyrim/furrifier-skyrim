"""Tests for the texconv batch encode path.

Batching one texconv.exe spawn across N PNGs instead of N spawns is
a ~100x speedup for facegen. The encoded DDSes must be byte-identical
(or at worst mipmap-count equivalent) to the single-shot path, otherwise
the live-run DDSes won't match CK's reference.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import Image


def _write_test_png(path: Path, color=(128, 64, 32, 255), size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (size, size), color).save(path)


def _dxgi_format(dds_path: Path) -> int:
    """Read the DXGI format out of the DDS DX10 header extension."""
    with open(dds_path, "rb") as f:
        data = f.read(148)
    return struct.unpack("<I", data[128:132])[0]


def test_batch_produces_bc7(tmp_path):
    from furrifier.facegen.texconv import encode_bc7_batch
    pngs = []
    for i in range(3):
        p = tmp_path / f"in_{i}.png"
        _write_test_png(p, color=(i * 60, 100, 255 - i * 60, 255))
        pngs.append(p)
    out = tmp_path / "out"
    dds_list = encode_bc7_batch(pngs, out)
    assert len(dds_list) == 3
    for dds in dds_list:
        assert dds.is_file()
        # BC7_UNORM = dxgi 98
        assert _dxgi_format(dds) == 98, f"wrong DXGI for {dds.name}"


def test_batch_size_matches_single_encode(tmp_path):
    """Batch and single-shot encodes must produce equivalent DDS size
    (i.e. same mipmap chain depth). The live facegen pipeline switched
    from single to batch; if the batch path dropped mipmaps, the live
    output would silently diverge from CK's reference while the fixture
    tests (which go through the single path) stayed green."""
    from furrifier.facegen.texconv import encode_bc7, encode_bc7_batch

    png = tmp_path / "sample.png"
    _write_test_png(png, size=256)

    single_dir = tmp_path / "single"
    single_dds = encode_bc7(png, single_dir)

    batch_dir = tmp_path / "batch"
    batch_dds = encode_bc7_batch([png], batch_dir)[0]

    # Allow a few bytes of variance across texconv versions but catch
    # the "lost half the mipmap chain" regression — that's thousands
    # of bytes on a 256x256 BC7.
    size_delta = abs(single_dds.stat().st_size - batch_dds.stat().st_size)
    assert size_delta < 256, (
        f"batch DDS differs from single by {size_delta} bytes "
        f"(single={single_dds.stat().st_size}, batch={batch_dds.stat().st_size})"
    )


def test_batch_empty_input_noop(tmp_path):
    """Passing an empty list must not spawn texconv and must not crash."""
    from furrifier.facegen.texconv import encode_bc7_batch
    result = encode_bc7_batch([], tmp_path / "out")
    assert result == []
