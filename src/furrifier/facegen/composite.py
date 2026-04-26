"""
Phase 2b: composite an NPC's tint layers into one RGBA overlay.

Alpha-composites each tint layer (mask grayscale x TINC color x TINV
intensity) onto an accumulator. The result feeds the in-process BC7
encoder (``dds.write_bc7_dds``) for the final FaceTint DDS — no PNG
intermediate, no subprocess.

Missing mask files (mod references a path we can't resolve loose or
from any BSA) are skipped with a warning — losing a layer's detail
is strictly preferable to bailing the whole NPC on one bad reference.

This is a *first approximation* of CK's behavior — simple alpha-over
composite. CK's actual blend stack may differ per layer-class (warpaint,
dirt, skin tone, etc.). We'll validate against the CK-baked reference
FaceTint dds and iterate blend modes if the numerical diff is too large.

Two entry points:

- `build_facetint_dds(npc_info, resolver, out_dir, output_size=None)`
  is the live API used by the furrifier pipeline. Takes an NPC-info
  dict (same shape as one manifest entry) and an AssetResolver.
- `composite_to_png_and_dds(data_root, form_id, out_dir, output_size)`
  is the test/CLI wrapper: loads `manifest.json`, spins up a
  loose-only resolver rooted at `data_root`, and delegates.
"""
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from .assets import AssetResolver


log = logging.getLogger("furrifier.facegen.composite")

HERE = Path(__file__).parent
# CLI mode resolves data_root relative to the tests fixture tree.
_TEST_FACEGEN_ROOT = Path(__file__).resolve().parents[3] / "tests" / "facegen"
OUT_DIR = _TEST_FACEGEN_ROOT / "out_tints"


def load_mask_rgba(path: Path, target_size: int | None = None) -> np.ndarray:
    """Load a DDS/PNG/JPG as RGBA float32 in [0, 1], optionally resampled
    to target_size x target_size via Lanczos. Vanilla masks are 512x512;
    pass a bigger target_size to upscale for higher-resolution output."""
    im = Image.open(path).convert("RGBA")
    if target_size is not None and im.size != (target_size, target_size):
        im = im.resize((target_size, target_size), Image.Resampling.LANCZOS)
    return np.asarray(im, dtype=np.float32) / 255.0


def load_mask_coverage(path: Path, target_size: int | None = None) -> np.ndarray:
    """Load a mask and return its grayscale coverage (2D float32 in [0, 1]).

    Tint masks ship as RGB (alpha always 255), with grayscale coverage
    encoded in the RGB channels. Every compositor use eventually does
    `mask[..., 0] * 0.299 + mask[..., 1] * 0.587 + mask[..., 2] * 0.114`
    to extract that coverage; doing it once at load time (and caching the
    result) saves redundant luminance math across NPCs and shrinks the
    cache footprint by 4x."""
    rgba = load_mask_rgba(path, target_size=target_size)
    return (rgba[..., 0] * 0.299 + rgba[..., 1] * 0.587
            + rgba[..., 2] * 0.114)


# RACE's TINP (Tint Mask Type) code for the SkinTone layer.
TINP_SKIN_TONE = 6

# Power-of-2 output sizes the compositor supports.
VALID_OUTPUT_SIZES = (256, 512, 1024, 2048, 4096)


def composite_layers(resolver: AssetResolver, tints: list[dict],
                     base_color: list | None = None,
                     output_size: int | None = None) -> np.ndarray:
    """Alpha-composite each tint layer onto an RGBA accumulator.

    If output_size is given, all masks are resampled to that size via
    Lanczos. Otherwise the first mask's native size is used (vanilla
    Skyrim = 512x512).

    Skin-tone handling (first):
      - If the NPC has a tint layer whose race-level TINP=6 (Skin Tone),
        use that layer's mask with QNAM as the color at full intensity.
      - Otherwise, solid-fill QNAM (as if the mask were pure white).
      The NPC's TINC/TINV on that layer are ignored — QNAM is the
      already-resolved authoritative color. TINC/TINV only drive the
      runtime RaceMenu sliders; they aren't baked into the face tint DDS.

    Other layers:
      - Mask's RGB luminance (alpha is uninformative on vanilla masks)
        gives per-pixel coverage.
      - Contribution alpha = mask_coverage * TINV.
      - Contribution color = TINC.
      - Premultiplied alpha-over onto the accumulator.
    """
    if not tints:
        raise ValueError("no tint layers")

    # Run-scoped mask cache (key = (relpath_normalized, target_size)).
    # Many NPCs of the same race share masks, and Pillow's DDS decoder
    # is the hottest path in the facegen run — caching here takes a
    # whole-run profile from 300+s to seconds of mask I/O.
    mask_cache: dict = resolver.image_cache

    def resolve_or_warn(relpath: str) -> Optional[Path]:
        """Return the resolved path, or None + a warning. Individual
        missing masks must not bail the whole NPC — the other layers
        composite fine on their own."""
        p = resolver.resolve(relpath)
        if p is None:
            log.warning("tint mask not found, skipping: %s", relpath)
        return p

    def load_cached(relpath: str, target_size: Optional[int]) -> Optional[np.ndarray]:
        """Resolve + load + cache. Returns the 2D coverage (luminance)
        array for the mask, or None if the mask can't be resolved.
        Cache is keyed by (relpath, target_size); coverage is computed
        exactly once per unique mask across the whole run."""
        key = (relpath.replace("/", "\\").lower(), target_size)
        cached = mask_cache.get(key)
        if cached is not None:
            return cached
        p = resolve_or_warn(relpath)
        if p is None:
            return None
        cov = load_mask_coverage(p, target_size=target_size)
        mask_cache[key] = cov
        return cov

    # Determine canvas size: output_size if supplied, else first
    # RESOLVABLE mask's native size. (Can't just use tints[0] — it might
    # be the one that's missing.) Fallback when nothing resolves:
    # vanilla 512x512. Read only the header via Image.open — PIL is lazy
    # and won't decode pixels without .convert/.load, so the probe is
    # near-free. Skipping the decode here is what keeps the first
    # resolvable mask from being decoded twice (once for probe, once
    # for composite).
    if output_size is None:
        h = w = 512
        for t in tints:
            p = resolver.resolve(t["mask"])
            if p is not None:
                with Image.open(p) as im:
                    w, h = im.size
                break
    else:
        h = w = output_size

    # Split the accumulator into contiguous RGB and alpha arrays for the
    # inner loop. Working through `acc[..., :3]` strided views is ~30%
    # of the compositor run time; contiguous ops are much faster. Pre-
    # allocate three scratch buffers (tmp3 for per-layer RGB math, tmp1a
    # for contrib_a, tmp1b for the alpha blend) so the hot loop makes
    # zero fresh allocations — down from six per layer.
    acc_rgb = np.zeros((h, w, 3), dtype=np.float32)
    acc_a = np.zeros((h, w), dtype=np.float32)
    tmp3 = np.empty((h, w, 3), dtype=np.float32)
    tmp1a = np.empty((h, w), dtype=np.float32)
    tmp1b = np.empty((h, w), dtype=np.float32)

    # Find the NPC's skin-tone layer (TINP=6) if present
    skin_layer = next((t for t in tints if t.get("tinp") == TINP_SKIN_TONE), None)

    if base_color is None:
        base_rgb = np.zeros(3, dtype=np.float32)
    else:
        base_rgb = np.asarray(base_color[:3], dtype=np.float32) / 255.0

    # Seed the accumulator with the skin-tone layer (QNAM color through
    # the SkinTone mask). If the mask can't be resolved, fall back to a
    # solid QNAM fill — same behavior as NPCs with no TINP=6 entry.
    skin_cov = load_cached(skin_layer["mask"], w) if skin_layer else None
    if skin_cov is not None:
        np.multiply(base_rgb, skin_cov[..., None], out=acc_rgb)
        acc_a[:] = skin_cov
    elif base_color is not None:
        acc_rgb[:] = base_rgb
        acc_a.fill(1.0)

    for layer in tints:
        # Skip the skin-tone layer — already handled above via QNAM.
        if layer.get("tinp") == TINP_SKIN_TONE:
            continue
        cov = load_cached(layer["mask"], w)
        if cov is None:
            continue

        color = np.asarray(layer["color"][:3], dtype=np.float32) / 255.0
        intensity = float(layer["intensity"])

        # contrib_a = cov * intensity  (cov comes from the shared cache;
        # don't mutate it — write into our scratch buffer instead)
        np.multiply(cov, intensity, out=tmp1a)

        # acc_rgb += contrib_a * (color - acc_rgb)
        # Algebraically identical to the classic alpha-over formula
        # (color*contrib_a + (1-contrib_a)*acc_rgb) but skips the
        # separate contrib_rgb intermediate array.
        np.subtract(color, acc_rgb, out=tmp3)
        tmp3 *= tmp1a[..., None]
        acc_rgb += tmp3

        # acc_a = contrib_a + (1 - contrib_a) * acc_a
        #       = acc_a + contrib_a * (1 - acc_a)
        np.subtract(1.0, acc_a, out=tmp1b)
        tmp1b *= tmp1a
        acc_a += tmp1b

    # Assemble the (H, W, 4) output. This is the one strided write we
    # can't avoid without changing the function's return contract — but
    # it happens once per NPC, not once per layer.
    acc = np.empty((h, w, 4), dtype=np.float32)
    acc[..., :3] = acc_rgb
    acc[..., 3] = acc_a
    return acc


def _composite_to_uint8(npc_info: dict, resolver: AssetResolver,
                        output_size: Optional[int]) -> np.ndarray:
    """Composite the NPC's tint layers and return a uint8 RGBA array
    ready for either PNG save or in-process BC7 encode."""
    form_id = npc_info["form_id"]
    if output_size is not None and output_size not in VALID_OUTPUT_SIZES:
        raise ValueError(
            f"output_size {output_size} not in {VALID_OUTPUT_SIZES}")
    tints = npc_info.get("tints") or []
    if not tints:
        raise ValueError(f"NPC {form_id} has no tint layers to composite")
    acc = composite_layers(resolver, tints,
                           base_color=npc_info.get("qnam_color"),
                           output_size=output_size)
    return np.clip(acc * 255.0, 0, 255).astype(np.uint8)


def build_facetint_png(npc_info: dict, resolver: AssetResolver,
                       out_dir: Path,
                       output_size: Optional[int] = None) -> Path:
    """Composite an NPC's tint layers and save the result as PNG.
    Kept for tests and any caller that still wants the intermediate
    PNG (single-NPC CLI, debugging). Production runs use
    ``build_facetint_dds`` to skip the PNG round-trip entirely."""
    as_u8 = _composite_to_uint8(npc_info, resolver, output_size)
    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{npc_info['form_id']}.png"
    Image.fromarray(as_u8, "RGBA").save(png_path)
    return png_path


def build_facetint_dds(npc_info: dict, resolver: AssetResolver,
                       out_dir: Path,
                       output_size: Optional[int] = None) -> Path:
    """Composite + BC7-encode one NPC's face tint directly to disk,
    no PNG round-trip. Replaces the old PNG → texconv subprocess path:
    encode happens in-process via the vendored ``bc7enc`` library
    (``furrifier/native/bc7enc/``). Returns the DDS path."""
    from .dds import write_bc7_dds

    as_u8 = _composite_to_uint8(npc_info, resolver, output_size)
    out_dir.mkdir(parents=True, exist_ok=True)
    dds_path = out_dir / f"{npc_info['form_id']}.dds"
    write_bc7_dds(dds_path, as_u8)
    return dds_path


def composite_to_png_and_dds(data_root: Path, form_id: str,
                             out_dir: Path,
                             output_size: int | None = None) -> tuple[Path, Path]:
    """Legacy manifest-driven entry point. Reads `manifest.json` from
    `data_root`, finds the NPC by form_id, and spins up a loose-only
    resolver rooted at `data_root`. Used by tests and the CLI."""
    manifest = json.loads((data_root / "manifest.json").read_text())
    entry = next((n for n in manifest["npcs"] if n["form_id"] == form_id), None)
    if entry is None:
        raise SystemExit(f"no NPC with form_id={form_id}")

    print(f"[npc] {entry['label']} 0x{entry['form_id']} "
          f"race={entry.get('race_edid')} female={entry.get('is_female')}")
    print(f"[npc] QNAM base color = {entry.get('qnam_color')}")
    print(f"[npc] {len(entry['tints'])} tint layers")
    for t in entry["tints"]:
        name = Path(t["mask"]).name
        print(f"       tini={t['tini']:3d} color={tuple(t['color'])} "
              f"v={t['intensity']:.2f}  {name}")

    with AssetResolver(data_root, bsa_readers=[]) as resolver:
        png_path = build_facetint_png(
            entry, resolver, out_dir, output_size=output_size)
        dds_path = build_facetint_dds(
            entry, resolver, out_dir, output_size=output_size)
    print(f"[png]  {png_path} ({png_path.stat().st_size} bytes)")
    print(f"[dds]  {dds_path} ({dds_path.stat().st_size} bytes)")
    return png_path, dds_path


if __name__ == "__main__":
    # Usage: composite_tint.py [data_root_name] [form_id] [output_size]
    data_root_name = sys.argv[1] if len(sys.argv) > 1 else "Data_vanilla"
    form_id = sys.argv[2] if len(sys.argv) > 2 else "0001327C"
    output_size = int(sys.argv[3]) if len(sys.argv) > 3 else None
    suffix = f"_size{output_size}" if output_size else ""
    composite_to_png_and_dds(
        _TEST_FACEGEN_ROOT / data_root_name, form_id,
        OUT_DIR / f"{data_root_name}{suffix}",
        output_size=output_size,
    )
