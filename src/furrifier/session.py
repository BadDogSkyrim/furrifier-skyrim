"""Shared state for the furrification pipeline.

Splits the previously monolithic `run_furrification` into three
reusable pieces:

- :func:`setup_session` does the once-per-run work: loads plugins,
  builds the FurryContext, runs race-level furrification (races,
  headpart lists, presets). Returns a :class:`FurrificationSession`
  holding everything downstream stages need.
- :meth:`FurryContext.furrify_npc` (already on the context) turns
  one NPC record into a patched override inside the session's patch.
  Callable repeatedly — the Run path iterates every NPC; the
  preview path will call it one-at-a-time as the user picks NPCs.
- :func:`bake_facegen_for` runs the single-NPC facegen pipeline
  (extract + nif + tint) into a target directory. Used by both the
  full Run (indirectly, via build_facegen_for_patch) and the live
  preview.

`run_furrification` in main.py is now a thin orchestrator over these
primitives; user-visible behavior is unchanged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

from esplib import (
    LoadOrder,
    Plugin,
    PluginSet,
    find_game_data,
    find_strings_dir,
)
from esplib.record import Record

from .config import FurrifierConfig
from .context import FurryContext
from .furry_load import (
    build_race_headparts,
    build_race_tints,
    load_headparts,
    load_races,
)
from .race_defs import load_scheme
from .vanilla_setup import setup_vanilla


log = logging.getLogger(__name__)


ProgressCallback = Callable[[str], None]


@dataclass
class FurrificationSession:
    """State shared between the Run path and the live-preview path.

    Setup cost (load plugins, furrify races) is amortized across any
    number of per-NPC operations — preview clicks are cheap after the
    first setup.
    """
    config: FurrifierConfig
    data_dir: Path
    output_dir: Path
    plugin_set: PluginSet
    patch: Plugin
    context: FurryContext


@dataclass
class LoadedPlugins:
    """Result of the plugin-loading stage (see `load_plugins`).

    Separate from :class:`FurrificationSession` because plugin load is
    the expensive part (~15s on a real load order) and is *scheme-
    independent* — the live-preview worker caches this across scheme
    changes and only re-runs the cheap race-furrification stage.
    """
    plugin_set: PluginSet
    data_dir: Path
    output_dir: Path


def load_plugins(
        config: FurrifierConfig,
        load_order: Optional[LoadOrder] = None,
        progress: Optional[ProgressCallback] = None,
) -> LoadedPlugins:
    """Expensive plugin-load stage. No scheme involvement, no patch
    yet. Returns handles the scheme-dependent stage consumes."""
    def emit(phase: str) -> None:
        if progress is not None:
            progress(phase)

    if config.game_data_dir:
        data_dir = Path(config.game_data_dir)
    else:
        data_dir = find_game_data("tes5")
    if data_dir is None:
        raise RuntimeError(
            "Could not find Skyrim installation. Set game_data_dir.")

    if config.output_dir:
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = data_dir

    emit("Loading plugins")
    log.info("Loading plugins...")
    patch_name = config.patch_filename.lower()
    if load_order is None:
        load_order = LoadOrder.from_game("tes5", active_only=True)
    load_order.plugins = [p for p in load_order.plugins
                          if p.lower() != patch_name]
    plugin_set = PluginSet(load_order)
    string_dirs = []
    strings_dir = find_strings_dir("tes5")
    if strings_dir:
        string_dirs.append(str(strings_dir))
    game_strings = data_dir / "Strings"
    if game_strings.is_dir() and str(game_strings) not in string_dirs:
        string_dirs.append(str(game_strings))
    plugin_set.string_search_dirs = string_dirs
    plugin_set.load_all()
    log.info("Loaded %d plugins", len(plugin_set))

    return LoadedPlugins(
        plugin_set=plugin_set,
        data_dir=data_dir,
        output_dir=output_dir,
    )


def build_session_over_plugins(
        config: FurrifierConfig,
        plugins: LoadedPlugins,
        progress: Optional[ProgressCallback] = None,
) -> FurrificationSession:
    """Scheme-dependent stage: build ctx, patch, furrify races, inject
    patch into plugin_set. Cheap (~1-2s) compared to `load_plugins`.

    If the plugin_set already has a patch of the same filename
    injected (from a previous scheme), it's removed first so the new
    patch can take over — otherwise FormID resolution would see both
    and return the stale one.
    """
    from .facegen import (_inject_patch_into_plugin_set,
                          _uninject_patch_from_plugin_set)

    def emit(phase: str) -> None:
        if progress is not None:
            progress(phase)

    plugin_set = plugins.plugin_set
    data_dir = plugins.data_dir
    output_dir = plugins.output_dir

    emit("Loading scheme")
    log.info("Loading scheme '%s'...", config.race_scheme)
    ctx = load_scheme(config.race_scheme)
    setup_vanilla(ctx)

    emit("Loading races and headparts")
    log.info("Loading races...")
    races_by_edid = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid.items()}
    log.info("Loading headparts...")
    headparts = load_headparts(plugin_set, ctx)
    log.info("Building race-headpart index...")
    race_headparts = build_race_headparts(list(plugin_set), headparts)
    log.info("Building race-tint index...")
    race_tints = build_race_tints(list(plugin_set))

    # Pull any prior patch out of the override index so the new one
    # fully replaces it. No-op on a fresh plugin_set.
    _uninject_patch_from_plugin_set(plugin_set, config.patch_filename)

    patch_path = output_dir / config.patch_filename
    patch = Plugin.new_plugin(patch_path)
    patch.plugin_set = plugin_set

    furry = FurryContext(
        patch=patch,
        ctx=ctx,
        races=races,
        all_headparts=headparts,
        race_headparts=race_headparts,
        race_tints=race_tints,
        plugin_set=plugin_set,
        max_tint_layers=config.max_tint_layers,
    )

    emit("Furrifying races")
    log.info("Furrifying races...")
    race_count = furry.furrify_all_races()
    log.info("Furrified %d races", race_count)

    emit("Furrifying headpart lists")
    log.info("Furrifying headpart lists...")
    flst_count = furry.furrify_all_headpart_lists(plugin_set)
    log.info("Modified %d headpart FormLists", flst_count)

    emit("Furrifying race presets")
    log.info("Furrifying race presets...")
    preset_count = furry.furrify_race_presets(plugin_set)
    log.info("Created %d race preset NPCs", preset_count)

    _inject_patch_into_plugin_set(plugin_set, patch)

    return FurrificationSession(
        config=config,
        data_dir=data_dir,
        output_dir=output_dir,
        plugin_set=plugin_set,
        patch=patch,
        context=furry,
    )


def setup_session(
        config: FurrifierConfig,
        load_order: Optional[LoadOrder] = None,
        progress: Optional[ProgressCallback] = None,
) -> FurrificationSession:
    """Convenience wrapper: load plugins then build a session over
    them. Most callers just want the session; the two-stage form is
    for the live-preview worker, which caches plugins across scheme
    changes to avoid paying for the re-load.

    Raises :class:`RuntimeError` if Skyrim's Data folder can't be
    located.
    """
    plugins = load_plugins(config, load_order=load_order, progress=progress)
    return build_session_over_plugins(config, plugins, progress=progress)


def bake_facegen_for(
        npc: Record,
        session: FurrificationSession,
        out_dir: Optional[Path] = None,
) -> Tuple[Path, Optional[Path]]:
    """Bake one NPC's facegen nif (+ face tint DDS if any) into
    ``out_dir``. Used by the live-preview path; the full Run path
    batches through :func:`facegen.build_facegen_for_patch` instead.

    ``out_dir`` defaults to a pair of directories named after the
    NPC's originating plugin under the session's FaceGenData tree.
    Passing an explicit temp dir is the preview flow — we don't want
    single-NPC previews to scatter files into the real output tree.

    Returns ``(nif_path, dds_path_or_None)``. ``dds_path`` is None
    when the NPC has no tint layers (e.g. some presets).
    """
    from .facegen import (
        AssetResolver,
        base_plugin_for,
        build_facegen_nif,
        build_facetint_dds,
        extract_npc_info,
    )

    patch = session.patch
    base_plugin = base_plugin_for(npc, patch)

    if out_dir is None:
        facegeom_dir = (session.output_dir / "meshes" / "actors" / "character"
                        / "FaceGenData" / "FaceGeom" / base_plugin)
        facetint_dir = (session.output_dir / "textures" / "actors" / "character"
                        / "FaceGenData" / "FaceTint" / base_plugin)
    else:
        # Single flat directory for previews — preview doesn't need
        # the per-plugin folder split. NIF references the tint DDS by
        # its FaceTint-subfolder relpath stamped into the shader, so
        # for live-preview use the caller should stage both files at
        # the FaceGenData-tree-relative paths the viewer expects.
        facegeom_dir = (out_dir / "meshes" / "actors" / "character"
                        / "FaceGenData" / "FaceGeom" / base_plugin)
        facetint_dir = (out_dir / "textures" / "actors" / "character"
                        / "FaceGenData" / "FaceTint" / base_plugin)

    info = extract_npc_info(npc, session.plugin_set, base_plugin)
    form_id = info["form_id"]

    with AssetResolver.for_data_dir(session.data_dir) as resolver:
        nif_path = facegeom_dir / f"{form_id}.nif"
        build_facegen_nif(info, resolver, nif_path)
        if info.get("tints"):
            _, dds_path = build_facetint_dds(info, resolver, facetint_dir)
        else:
            dds_path = None
    return nif_path, dds_path
