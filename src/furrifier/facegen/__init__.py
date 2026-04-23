"""FaceGen engine package.

The public API is the top-level driver `build_facegen_for_patch`, which
iterates every NPC override the furrifier wrote into the patch and
produces the per-NPC `.nif` + `.dds` under the game's FaceGenData tree.

Lower-level entry points (used by tests and the CLI) live in the
sibling modules:
  - `assemble.build_facegen_nif(npc_info, resolver, dst_path)`
  - `composite.build_facetint_dds(npc_info, resolver, out_dir, output_size)`
  - `extract.extract_npc_info(npc_record, plugin_set, patch_plugin_name)`
  - `assets.AssetResolver` — loose/BSA asset lookup.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

import struct

from esplib import PluginSet

from ..npc import inherits_traits
from .assets import AssetResolver
from .assemble import build_facegen_nif
from .composite import build_facetint_dds, build_facetint_png
from .extract import extract_npc_info
from .texconv import encode_bc7_batch


log = logging.getLogger("furrifier.facegen")


# ACBS flag bit for "Is CharGen Face Preset" (per tes5.py defs) — these
# are the character-creator preset NPCs that don't render in-world, so
# they don't need their own facegen bake.
_CHARGEN_PRESET_BIT = 1 << 2


def base_plugin_for(npc, patch) -> str:
    """Return the plugin filename that 'owns' the NPC for FaceGenData
    pathing purposes — the plugin that DEFINED the record, not the
    plugin that currently overrides it.

    For an override of a Skyrim.esm NPC: returns 'Skyrim.esm' even
    when the winning override lives in the patch. For a record newly
    created by the furrifier (file_index == len(patch.masters)):
    returns the patch's filename. Matches the FaceGenData layout CK
    produces.
    """
    idx = npc.form_id.file_index
    masters = patch.header.masters
    if idx < len(masters):
        return masters[idx]
    return patch.file_path.name


def _is_chargen_preset(npc) -> bool:
    try:
        return bool(npc["ACBS"]["flags"].IsCharGenFacePreset)
    except Exception:
        acbs = npc.get_subrecord("ACBS")
        if acbs is None or len(acbs.data) < 4:
            return False
        return bool(struct.unpack("<I", acbs.data[:4])[0]
                    & _CHARGEN_PRESET_BIT)


ProgressCallback = Callable[[str], None]


def _uninject_patch_from_plugin_set(plugin_set: PluginSet,
                                    patch_name: str) -> None:
    """Undo a previous `_inject_patch_into_plugin_set` for
    `patch_name`. Used when rebuilding a session against a cached
    plugin_set — the old patch must come out before the new one
    goes in, or both coexist and FormID resolution returns whichever
    wins the stale override chain. No-op if the name isn't injected.
    """
    plugin_set._plugins.pop(patch_name, None)
    plugin_set._loaded_full.pop(patch_name, None)
    try:
        plugin_set.load_order.plugins.remove(patch_name)
    except ValueError:
        pass
    plugin_set._override_index = None


def _inject_patch_into_plugin_set(plugin_set: PluginSet, patch) -> None:
    """Add the freshly-saved in-memory patch to `plugin_set` so
    FormID resolution sees its RACE / HDPT / etc. overrides.

    `main.py` strips the patch from the load order before plugin
    loading — the patch often doesn't exist yet on first run, and
    stale copies would poison the master chain. Once furrification
    is done and `patch.save()` has returned, the in-memory Plugin
    holds every override we want extract to see; inject it directly
    rather than re-reading from disk. Without this, an NPC override
    whose RNAM still points at (say) NordRace resolves to the
    **vanilla** NordRace — and extract hands the facegen builder
    vanilla human headparts instead of the patched furry head data.
    """
    name = patch.file_path.name
    if name in plugin_set._plugins:
        return
    plugin_set.load_order.plugins.append(name)
    plugin_set._plugins[name] = patch
    plugin_set._loaded_full[name] = True
    # Invalidate the cached override index so the next query rebuilds
    # it with the patch's records included.
    plugin_set._override_index = None


def build_facegen_for_patch(
        patch,
        plugin_set: PluginSet,
        data_dir: Path,
        output_dir: Optional[Path] = None,
        progress: Optional[ProgressCallback] = None,
        limit: Optional[int] = None) -> tuple[int, int]:
    """Build FaceGen files for every NPC override in `patch`.

    `data_dir` is the Skyrim install Data folder — source of headpart
    nifs, chargen tris, tint masks (loose or BSA). `output_dir` is
    where the generated files land; defaults to `data_dir`. Keeping
    them separate lets callers write into a mod-manager staging folder
    without polluting the live Data tree.

    `limit` caps the number of NPCs we bake to the first N (after
    filtering out CharGen face presets). None = no cap. Useful for
    previewing a scheme's output on a small subset before committing
    to a full-load-order run.

    Writes into output_dir:
      meshes/actors/character/FaceGenData/FaceGeom/<patch>/<formid>.nif
      textures/actors/character/FaceGenData/FaceTint/<patch>/<formid>.dds

    Any per-NPC failure is logged and counted; we don't abort the run
    because one weird NPC (missing headpart mesh, broken tint mask)
    shouldn't torpedo thousands of others.

    Returns (succeeded, failed).
    """
    data_dir = Path(data_dir)
    output_dir = Path(output_dir) if output_dir is not None else data_dir

    # Make the patch's RACE / HDPT overrides visible to extract. Without
    # this, extract resolves every reference through the vanilla chain
    # and hands the builder vanilla headparts for furrified NPCs.
    _inject_patch_into_plugin_set(plugin_set, patch)

    facegeom_root = (output_dir / "meshes" / "actors" / "character"
                     / "FaceGenData" / "FaceGeom")
    facetint_root = (output_dir / "textures" / "actors" / "character"
                     / "FaceGenData" / "FaceTint")

    raw = list(patch.get_records_by_signature("NPC_"))
    # CharGen face presets are character-creator-only — never rendered
    # in-world, so baking facegen for them is wasted work.
    npcs = [n for n in raw if not _is_chargen_preset(n)]
    skipped_preset = len(raw) - len(npcs)
    if skipped_preset:
        log.info("FaceGen: skipping %d CharGen face preset NPCs", skipped_preset)
    # Trait-templated NPCs render using their template's facegen at
    # runtime, so baking a shell for them produces an empty nif the
    # game never reads. Skip entirely.
    before_trait_filter = len(npcs)
    npcs = [n for n in npcs if not inherits_traits(n)]
    skipped_trait = before_trait_filter - len(npcs)
    if skipped_trait:
        log.info("FaceGen: skipping %d trait-templated NPCs", skipped_trait)
    # Apply user-requested cap. Default (None) runs everything.
    if limit is not None and len(npcs) > limit:
        log.info("FaceGen: limit=%d — baking first %d of %d NPCs",
                 limit, limit, len(npcs))
        npcs = npcs[:limit]
    total = len(npcs)
    if total == 0:
        log.info("FaceGen: no NPCs in patch; nothing to do")
        return 0, 0

    log.info("FaceGen: building for %d NPCs", total)
    succeeded = 0
    failed = 0

    # Wall-clock totals per phase so we can see where time actually
    # goes for 1000+ NPCs. Cheap to track; printed at the end.
    t_extract = 0.0
    t_nif = 0.0
    t_png = 0.0
    t_run_start = time.perf_counter()

    # Collected PNGs for a batched texconv pass at the end. Keyed by
    # the final output directory so we encode each base-plugin folder
    # independently — BC7 output lands next to the PNG input.
    pngs_by_dir: dict[Path, list[Path]] = {}

    # One resolver for the run — the BSA extraction cache builds up
    # across NPCs, so shared vanilla headpart nifs only get pulled once.
    with AssetResolver.for_data_dir(data_dir) as resolver:
        for i, npc in enumerate(npcs):
            if progress:
                progress(f"FaceGen {i + 1}/{total}")
            edid_for_log = npc.editor_id or f"0x{int(npc.form_id):08X}"
            try:
                base_plugin = base_plugin_for(npc, patch)
                t0 = time.perf_counter()
                info = extract_npc_info(npc, plugin_set, base_plugin)
                t1 = time.perf_counter()
                form_id = info["form_id"]
                build_facegen_nif(info, resolver,
                                  facegeom_root / base_plugin / f"{form_id}.nif")
                t2 = time.perf_counter()
                if info.get("tints"):
                    tint_dir = facetint_root / base_plugin
                    png = build_facetint_png(info, resolver, tint_dir)
                    pngs_by_dir.setdefault(tint_dir, []).append(png)
                t3 = time.perf_counter()
                t_extract += t1 - t0
                t_nif += t2 - t1
                t_png += t3 - t2
                succeeded += 1
            except Exception as exc:
                log.warning("FaceGen skipped %s: %s", edid_for_log, exc)
                failed += 1

        # Batch BC7-encode every folder's PNGs. One texconv.exe spawn
        # per base-plugin folder (with chunking for CLI length limits)
        # instead of one per NPC — this is the big speedup.
        t_tex_start = time.perf_counter()
        total_pngs = sum(len(v) for v in pngs_by_dir.values())
        for folder, pngs in pngs_by_dir.items():
            log.info("FaceGen: batch-encoding %d DDSes under %s/",
                     len(pngs), folder.name)
            # Chunk ~200 files per spawn to stay safely under the
            # Windows CreateProcess command-line limit even with long
            # paths.
            _CHUNK = 200
            for start in range(0, len(pngs), _CHUNK):
                encode_bc7_batch(pngs[start:start + _CHUNK], folder)
        t_encode = time.perf_counter() - t_tex_start

    t_total = time.perf_counter() - t_run_start
    log.info("FaceGen: %d succeeded, %d failed in %.1fs (%d DDSes encoded)",
             succeeded, failed, t_total, total_pngs)
    if succeeded:
        log.info("FaceGen phase totals (avg/NPC ms):  "
                 "extract=%.0f  nif=%.0f  tint_png=%.0f  tex_batch=%.0f  total=%.0f",
                 1000 * t_extract / succeeded,
                 1000 * t_nif / succeeded,
                 1000 * t_png / succeeded,
                 1000 * t_encode / succeeded,
                 1000 * t_total / succeeded)
    return succeeded, failed


__all__ = [
    "AssetResolver",
    "base_plugin_for",
    "build_facegen_nif",
    "build_facetint_dds",
    "build_facetint_png",
    "build_facegen_for_patch",
    "extract_npc_info",
]
