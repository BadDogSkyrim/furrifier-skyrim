"""
Phase 2a: emit a BC3 DDS via Pillow so we can verify whether the file
itself is valid (opens in IceStorm's DDS Viewer / other strict tools).

Writes a distinctive 2048x2048 test tint (asymmetric color + alpha
gradient, easy to spot malformations) as BC3. Also patches the header
to work around Pillow's broken pitch/linear-size and mipcount fields
(see project_pillow_bug_report.md).

Output lands in tests/facegen/out/phase2a_pillow_bc3.dds. No game-folder
side effects — inspect the file in your preferred DDS viewer.
"""
import struct
from pathlib import Path

from PIL import Image


HERE = Path(__file__).parent
OUT_PATH = HERE / "out" / "phase2a_pillow_bc3.dds"


def make_test_pattern(size: int = 2048) -> Image.Image:
    """Green-left / red-right split with a top-to-bottom alpha gradient.
    Asymmetric so UV flips stand out; gradient so alpha-channel errors
    are visible."""
    img = Image.new("RGBA", (size, size))
    px = img.load()
    half = size // 2
    for y in range(size):
        a = int(y * 255 / (size - 1))
        for x in range(size):
            px[x, y] = (0, 255, 0, a) if x < half else (255, 0, 0, a)
    return img


def save_bc3_patched(img: Image.Image, path: Path) -> None:
    """Pillow's BC3 block data is correct but its DDS header has several
    wrong fields. Save via Pillow then rewrite the header bytes to what
    strict readers (IceStorm, texconv, game engines) expect.

    Note: the CK-emitted FaceTint reference is actually BC7, not BC3.
    Pillow can't write BC7. We write BC3 here as a compatibility probe —
    if this file opens in IceStorm, we know Pillow's block data plus a
    fixed header is workable; the remaining question is whether Skyrim
    accepts BC3 (same size and layout as BC7 but lower quality) for
    face tints or insists on BC7 specifically. That answers the
    Pillow-vs-texconv choice for Phase 2b.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="DDS", pixel_format="BC3")

    w, h = img.size
    bw = max(1, (w + 3) // 4)
    bh = max(1, (h + 3) // 4)
    linear_size = bw * bh * 16  # BC3 = 16 bytes per 4x4 block

    # DDS flag bits we need set for compressed + mipmapped texture
    DDSD_CAPS        = 0x00000001
    DDSD_HEIGHT      = 0x00000002
    DDSD_WIDTH       = 0x00000004
    DDSD_PIXELFORMAT = 0x00001000
    DDSD_MIPMAPCOUNT = 0x00020000
    DDSD_LINEARSIZE  = 0x00080000
    flags = (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH
             | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT | DDSD_LINEARSIZE)

    DXGI_FORMAT_BC3_UNORM = 77
    D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3

    with open(path, "r+b") as f:
        buf = bytearray(f.read(148))
        # Main DDS_HEADER
        struct.pack_into("<I", buf,  8, flags)          # flags
        struct.pack_into("<I", buf, 20, linear_size)    # pitchOrLinearSize
        struct.pack_into("<I", buf, 24, 1)              # depth (2D = 1)
        struct.pack_into("<I", buf, 28, 1)              # mipmapCount
        # PIXELFORMAT.rgbBitCount @ offset 88 must be 0 for FourCC
        struct.pack_into("<I", buf, 88, 0)
        # DX10 extended header starts at offset 128
        struct.pack_into("<I", buf, 128, DXGI_FORMAT_BC3_UNORM)          # dxgiFormat
        struct.pack_into("<I", buf, 132, D3D10_RESOURCE_DIMENSION_TEXTURE2D)  # resourceDimension
        struct.pack_into("<I", buf, 136, 0)             # miscFlag
        struct.pack_into("<I", buf, 140, 1)             # arraySize (single tex = 1)
        struct.pack_into("<I", buf, 144, 0)             # miscFlags2 (alpha mode = unknown)
        f.seek(0)
        f.write(bytes(buf))


if __name__ == "__main__":
    img = make_test_pattern(2048)
    save_bc3_patched(img, OUT_PATH)
    print(f"[write] {OUT_PATH} ({OUT_PATH.stat().st_size} bytes)")
    print("Inspect with IceStorm's DDS Viewer or similar to check validity.")
