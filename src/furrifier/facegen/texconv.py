"""
Thin wrapper around texconv.exe for encoding the FaceTint DDS files.

texconv is Microsoft's DirectXTex CLI (bundled at furrifier/tools/).
We use it for the final PNG -> BC7 DDS encode step of the Phase 2
tint pipeline; Pillow's BC3 writer is broken (see
project_pillow_bug_report.md) and Skyrim's face shader insists on
BC7 anyway.

Two entry points:
- `encode_bc7(png_path, out_dir)` — single file; returns the produced DDS path.
- `encode_bc7_batch(png_paths, out_dir)` — passes all inputs in one
  subprocess so per-invocation overhead amortizes across thousands of
  NPCs. Per-plugin batching at the caller keeps progress reporting clean.

Texconv preserves filename (swaps .png for .dds), so `out_dir/<stem>.dds`
is deterministic.
"""
import subprocess
import sys
from pathlib import Path
from typing import Iterable


# Dev: .../furrifier/src/furrifier/facegen/texconv.py
#   parents[0]=facegen  [1]=furrifier(inner)  [2]=src  [3]=furrifier(outer)
# Frozen: tools/ ships loose next to the exe (like schemes/ and races/).
if getattr(sys, "frozen", False):
    TEXCONV_EXE = Path(sys.executable).parent / "tools" / "texconv.exe"
else:
    TEXCONV_EXE = Path(__file__).resolve().parents[3] / "tools" / "texconv.exe"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    if not TEXCONV_EXE.is_file():
        raise FileNotFoundError(
            f"texconv.exe not found at {TEXCONV_EXE}. "
            "Download from https://github.com/microsoft/DirectXTex/releases "
            "and place at that path."
        )
    # Suppress the console window the frozen GUI exe would otherwise
    # pop each time texconv is spawned. Harmless (0) on non-Windows.
    return subprocess.run(
        [str(TEXCONV_EXE), *args],
        capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def encode_bc7(png_path: Path, out_dir: Path) -> Path:
    """Encode a single PNG as BC7_UNORM DDS. Returns output path."""
    png_path = Path(png_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([
        "-f", "BC7_UNORM",
        "-m", "0",         # full mipmap chain (matches CK-emitted face tints)
        "-y",              # overwrite
        "-nologo",
        "-o", str(out_dir),
        str(png_path),
    ])
    return out_dir / (png_path.stem + ".dds")


def encode_bc7_batch(png_paths: Iterable[Path], out_dir: Path) -> list[Path]:
    """Encode many PNGs in one texconv call.

    Cheap subprocess amortization — texconv iterates inputs internally and
    uses the GPU for BC7 encode, so batching is significantly faster than
    per-file calls for large sets.
    """
    paths = [Path(p) for p in png_paths]
    if not paths:
        return []
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    _run([
        "-f", "BC7_UNORM",
        "-m", "0",         # full mipmap chain (matches CK; same as encode_bc7)
        "-y",
        "-nologo",
        "-o", str(out_dir),
        *[str(p) for p in paths],
    ])
    return [out_dir / (p.stem + ".dds") for p in paths]


if __name__ == "__main__":
    # Smoke test: re-encode the phase2a test pattern.
    test_png = Path(__file__).parent / "out" / "phase2a_test_pattern.png"
    if not test_png.is_file():
        raise SystemExit(f"expected test PNG at {test_png}")
    out = encode_bc7(test_png, test_png.parent)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
