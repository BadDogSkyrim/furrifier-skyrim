"""
Phase 2b: composite an NPC's tint layers into one RGBA overlay.

Reads manifest.json, finds one NPC entry, iterates its tint layers, and
alpha-composites each (mask grayscale x TINC color x TINV intensity) onto
an accumulator. Saves as PNG, then encodes to BC7 DDS via texconv.

This is a *first approximation* of CK's behavior — simple alpha-over
composite. CK's actual blend stack may differ per layer-class (warpaint,
dirt, skin tone, etc.). We'll validate against the CK-baked reference
FaceTint dds and iterate blend modes if the numerical diff is too large.

Usage:
    python composite_tint.py                       # Dervenin (default)
    python composite_tint.py Data_vanilla 0001414D # Ulfric
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from texconv_wrapper import encode_bc7


HERE = Path(__file__).parent
OUT_DIR = HERE / "out_tints"


def load_mask_rgba(path: Path) -> np.ndarray:
    """Load a DDS/PNG/JPG as RGBA float32 in [0, 1]."""
    im = Image.open(path).convert("RGBA")
    return np.asarray(im, dtype=np.float32) / 255.0


# RACE's TINP (Tint Mask Type) code for the SkinTone layer.
TINP_SKIN_TONE = 6


def composite_layers(data_root: Path, tints: list[dict],
                     base_color: list | None = None) -> np.ndarray:
    """Alpha-composite each tint layer onto an RGBA accumulator.

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

    # Use first mask's resolution as the canvas size
    first_mask = load_mask_rgba(data_root / tints[0]["mask"])
    h, w = first_mask.shape[:2]
    acc = np.zeros((h, w, 4), dtype=np.float32)

    # Find the NPC's skin-tone layer (TINP=6) if present
    skin_layer = next((t for t in tints if t.get("tinp") == TINP_SKIN_TONE), None)

    if base_color is None:
        base_rgb = np.zeros(3, dtype=np.float32)
    else:
        base_rgb = np.asarray(base_color[:3], dtype=np.float32) / 255.0

    if skin_layer is not None:
        # Apply QNAM color through the skin-tone layer's mask
        skin_mask = load_mask_rgba(data_root / skin_layer["mask"])
        if skin_mask.shape[:2] != (h, w):
            im = Image.open(data_root / skin_layer["mask"]).convert("RGBA").resize((w, h))
            skin_mask = np.asarray(im, dtype=np.float32) / 255.0
        cov = skin_mask[..., 0] * 0.299 + skin_mask[..., 1] * 0.587 + skin_mask[..., 2] * 0.114
        acc[..., :3] = base_rgb[None, None, :] * cov[..., None]
        acc[..., 3] = cov
    elif base_color is not None:
        # No SkinTone layer → solid fill, as if mask were pure white
        acc[..., 0] = base_rgb[0]
        acc[..., 1] = base_rgb[1]
        acc[..., 2] = base_rgb[2]
        acc[..., 3] = 1.0

    for layer in tints:
        # Skip the skin-tone layer — already handled above via QNAM.
        if layer.get("tinp") == TINP_SKIN_TONE:
            continue
        mask = load_mask_rgba(data_root / layer["mask"])
        if mask.shape[:2] != (h, w):
            # Resize to canvas size if a mask differs (shouldn't happen for
            # vanilla; uniform 2048x2048 everywhere).
            im = Image.open(data_root / layer["mask"]).convert("RGBA").resize((w, h))
            mask = np.asarray(im, dtype=np.float32) / 255.0

        # Per-pixel coverage from the mask. Vanilla Skyrim tint masks ship
        # as RGB (no alpha variation — alpha is always 255); the actual
        # coverage is encoded in the RGB channels as grayscale. Use
        # luminance of RGB, not alpha.
        cov = mask[..., 0] * 0.299 + mask[..., 1] * 0.587 + mask[..., 2] * 0.114

        color = np.asarray(layer["color"][:3], dtype=np.float32) / 255.0
        intensity = float(layer["intensity"])
        contrib_a = cov * intensity
        contrib_rgb = color[None, None, :] * contrib_a[..., None]

        # Alpha-over (premultiplied: contrib is already color*alpha above)
        inv = 1.0 - contrib_a[..., None]
        acc[..., :3] = contrib_rgb + inv * acc[..., :3]
        acc[..., 3:4] = contrib_a[..., None] + inv * acc[..., 3:4]

    return acc


def composite_to_png_and_dds(data_root: Path, form_id: str,
                             out_dir: Path) -> tuple[Path, Path]:
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

    acc = composite_layers(data_root, entry["tints"],
                           base_color=entry.get("qnam_color"))
    as_u8 = np.clip(acc * 255.0, 0, 255).astype(np.uint8)

    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{form_id}.png"
    Image.fromarray(as_u8, "RGBA").save(png_path)
    print(f"[png]  {png_path} ({png_path.stat().st_size} bytes)")

    dds_path = encode_bc7(png_path, out_dir)
    print(f"[dds]  {dds_path} ({dds_path.stat().st_size} bytes)")
    return png_path, dds_path


if __name__ == "__main__":
    data_root_name = sys.argv[1] if len(sys.argv) > 1 else "Data_vanilla"
    form_id = sys.argv[2] if len(sys.argv) > 2 else "0001327C"
    composite_to_png_and_dds(
        HERE / data_root_name, form_id,
        OUT_DIR / data_root_name,
    )
