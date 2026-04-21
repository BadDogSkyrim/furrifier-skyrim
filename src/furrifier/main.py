"""Furrifier main entry point.

Loads plugins, applies race preference scheme, furrifies NPCs and armor,
saves patch file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from esplib import Plugin, PluginSet, LoadOrder, find_game_data, find_strings_dir

from .config import FurrifierConfig, build_parser, normalize_argv, setup_logging
from .context import FurryContext
from .race_defs import load_scheme
from .vanilla_setup import setup_vanilla
from .furry_load import load_races, load_headparts, build_race_headparts, build_race_tints

log = logging.getLogger(__name__)


ProgressCallback = Callable[[str], None]


def run_furrification(
    config: FurrifierConfig,
    load_order: Optional[LoadOrder] = None,
    progress: Optional[ProgressCallback] = None,
) -> int:
    """Run the full furrification pipeline.

    Parameters
    ----------
    config : FurrifierConfig
        All settings for this run.
    load_order : LoadOrder, optional
        If given, use this load order. Otherwise build one from the game's
        active plugins (excluding the patch itself).
    progress : callable, optional
        Called with a short phase label at each pipeline milestone
        (e.g. "Loading plugins", "Furrifying NPCs"). Use to drive a GUI
        status line. Logging to the module logger still happens either way.

    Returns
    -------
    int
        0 on success, non-zero on failure.
    """

    # Fresh warning/error counter per run (GUI may invoke this many times).
    log_counter = _LogCounter()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_counter)
    try:
        return _run_furrification_body(config, load_order, progress, log_counter)
    finally:
        root_logger.removeHandler(log_counter)


def _run_furrification_body(
    config: FurrifierConfig,
    load_order: Optional[LoadOrder],
    progress: Optional[ProgressCallback],
    log_counter: "_LogCounter",
) -> int:
    def emit(phase: str) -> None:
        if progress is not None:
            progress(phase)

    log.info("Skyrim Furrifier v0.1.0")
    log.info(f"  Scheme: {config.race_scheme}")
    log.info(f"  Patch: {config.patch_filename}")
    log.info(f"  NPCs: male={config.furrify_npcs_male}, "
             f"female={config.furrify_npcs_female}")
    log.info(f"  Armor: {config.furrify_armor}")
    log.info(f"  Schlongs: {config.furrify_schlongs}")

    # Find Skyrim
    if config.game_data_dir:
        data_dir = Path(config.game_data_dir)
    else:
        data_dir = find_game_data('tes5')
    if data_dir is None:
        log.error("Could not find Skyrim installation. Use --data-dir.")
        return 1

    # Output dir defaults to data_dir when unset. Kept separate so the
    # user can write the patch + facegen into a mod-manager staging
    # folder without polluting the live Data tree.
    if config.output_dir:
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    else:
        output_dir = data_dir

    log.debug(f"Data directory: {data_dir}")
    log.debug(f"Output directory: {output_dir}")

    # Load preference scheme
    emit("Loading scheme")
    ctx = load_scheme(config.race_scheme)
    setup_vanilla(ctx)

    # Build load order (exclude the patch itself) unless caller supplied one
    emit("Loading plugins")
    log.debug("Loading plugins...")
    patch_name = config.patch_filename.lower()
    if load_order is None:
        load_order = LoadOrder.from_game('tes5', active_only=True)
    load_order.plugins = [p for p in load_order.plugins
                          if p.lower() != patch_name]
    plugin_set = PluginSet(load_order)
    string_dirs = []
    strings_dir = find_strings_dir('tes5')
    if strings_dir:
        string_dirs.append(str(strings_dir))
    # Also check the game's own Strings directory (for CC plugins etc.)
    game_strings = data_dir / 'Strings'
    if game_strings.is_dir() and str(game_strings) not in string_dirs:
        string_dirs.append(str(game_strings))
    plugin_set.string_search_dirs = string_dirs
    plugin_set.load_all()
    log.debug(f"Loaded {len(plugin_set)} plugins")

    # Load race and headpart data
    emit("Loading races and headparts")
    races_by_edid = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid.items()}
    headparts = load_headparts(plugin_set, ctx)

    # Build race headpart and tint indices
    race_headparts = build_race_headparts(list(plugin_set), headparts)
    race_tints = build_race_tints(list(plugin_set))

    # Create patch (masters added lazily as FormIDs are remapped)
    patch_path = output_dir / config.patch_filename
    patch = Plugin.new_plugin(patch_path)
    patch.plugin_set = plugin_set

    # Build context
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

    # Furrify races
    emit("Furrifying races")
    log.info("Furrifying races...")
    race_count = furry.furrify_all_races()
    log.info(f"Furrified {race_count} races")

    # Furrify headpart FormLists (must be after race furrification)
    emit("Furrifying headpart lists")
    log.info("Furrifying headpart lists...")
    flst_count = furry.furrify_all_headpart_lists(plugin_set)
    log.info(f"Modified {flst_count} headpart FormLists")

    # Furrify race presets (must be after race furrification)
    emit("Furrifying race presets")
    log.info("Furrifying race presets...")
    preset_count = furry.furrify_race_presets(plugin_set)
    log.info(f"Created {preset_count} race preset NPCs")

    # Furrify NPCs
    if config.furrify_npcs_male or config.furrify_npcs_female:
        emit("Furrifying NPCs")
        log.info("Furrifying NPCs...")
        npc_count = furry.furrify_all_npcs(
            plugin_set,
            furrify_male=config.furrify_npcs_male,
            furrify_female=config.furrify_npcs_female,
        )
        log.info(f"Furrified {npc_count} NPCs")

    # Extend leveled NPC lists with furry duplicates
    if ctx.leveled_npc_groups:
        log.info("Extending leveled NPC lists...")
        new_count, list_count = furry.extend_leveled_npcs(plugin_set)
        log.info(
            f"Created {new_count} leveled-list NPCs across {list_count} lists")

    # Furrify armor
    if config.furrify_armor:
        emit("Merging armor overrides")
        log.info("Merging armor overrides...")
        merge_count = furry.merge_armor_overrides(plugin_set)
        log.info(f"Merged {merge_count} ARMO records")

        emit("Furrifying armor")
        log.info("Furrifying armor...")
        armor_count = furry.furrify_all_armor(plugin_set)
        log.info(f"Modified {armor_count} armor records")

    # Furrify schlongs
    if config.furrify_schlongs:
        from .schlongs import furrify_all_schlongs
        emit("Furrifying schlongs")
        log.info("Furrifying schlongs...")
        # Build maps needed by schlong furrification
        race_assignments = {a.vanilla_id: a.furry_id
                            for a in ctx.assignments.values()}
        furry_to_vanilla: dict[str, list[str]] = {}
        for a in ctx.assignments.values():
            furry_to_vanilla.setdefault(a.furry_id, []).append(a.vanilla_id)
        for sub in ctx.subraces.values():
            race_assignments[sub.name] = sub.furry_id
            furry_to_vanilla.setdefault(sub.furry_id, []).append(sub.name)
        furrify_all_schlongs(plugin_set, patch, race_assignments,
                             furry_to_vanilla, races)

    # Print statistics
    furry.print_statistics()

    # Save the patch first — the FaceGen engine reads nothing from it,
    # and saving before the (comparatively slow) FaceGen step means the
    # user always has a usable patch even if they Ctrl-C during bake.
    emit("Saving patch")
    patch.save()
    log.info(f"Saved patch: {patch_path}")

    # Build per-NPC FaceGenData (nif + DDS) under <output>/FaceGenData/
    # so the user doesn't have to open CK and Ctrl-F4. Source assets
    # (headpart nifs, tri files, tint masks) are resolved against
    # data_dir; the outputs land under output_dir.
    if config.build_facegen:
        emit("Building FaceGen")
        log.info("Building FaceGen...")
        from .facegen import build_facegen_for_patch
        _run_facegen(config, patch, plugin_set, data_dir, output_dir, progress)

    # Print warning/error summary (after FaceGen so its warnings roll up too)
    _print_log_summary(log_counter)

    # If a log file was configured, echo its full path as the last line
    # so the user doesn't have to hunt for it afterward.
    if config.log_file:
        log.info("Log written to: %s", Path(config.log_file).resolve())

    emit("Done")
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(sys.argv[1:]))
    config = FurrifierConfig.from_args(args)
    setup_logging(config)
    return run_furrification(config)


class _LogCounter(logging.Handler):
    """Counts warnings and errors for end-of-run summary."""

    def __init__(self):
        super().__init__()
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            self.errors.append(self.format(record))
        elif record.levelno >= logging.WARNING:
            self.warnings.append(self.format(record))


def _run_facegen(config, patch, plugin_set, data_dir, output_dir, progress):
    """Run the facegen builder, optionally under cProfile.

    When `config.profile_file` is set, dump raw stats to that path and
    print the top-30 cumulative-time functions so the bottleneck is
    visible without launching a viewer.
    """
    from .facegen import build_facegen_for_patch

    def _run():
        build_facegen_for_patch(patch, plugin_set,
                                data_dir=data_dir,
                                output_dir=output_dir,
                                progress=progress)

    if not config.profile_file:
        _run()
        return

    import cProfile
    import io
    import pstats
    profiler = cProfile.Profile()
    try:
        profiler.enable()
        _run()
    finally:
        profiler.disable()
        out_path = Path(config.profile_file).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        profiler.dump_stats(out_path)
        log.info("cProfile stats written to: %s", out_path)
        # Also surface the top 30 cumulative-time calls in the run log
        # so the obvious bottleneck is visible without a viewer.
        buf = io.StringIO()
        stats = pstats.Stats(profiler, stream=buf).sort_stats("cumulative")
        stats.print_stats(30)
        log.info("Top 30 cumulative-time calls:\n%s", buf.getvalue())


def _print_log_summary(counter: "_LogCounter") -> None:
    """Print summary of warnings and errors at end of run."""
    if counter.warnings:
        print(f"\n{len(counter.warnings)} warning(s):")
        for msg in counter.warnings:
            print(f"  {msg}")
    if counter.errors:
        print(f"\n{len(counter.errors)} error(s):")
        for msg in counter.errors:
            print(f"  {msg}")
    if not counter.warnings and not counter.errors:
        print("\nNo warnings or errors.")


if __name__ == '__main__':
    sys.exit(main())
