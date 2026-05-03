"""Furrifier main entry point.

Loads plugins, applies race preference scheme, furrifies NPCs and armor,
saves patch file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable, Optional

from esplib import LoadOrder

from .config import FurrifierConfig, build_parser, normalize_argv, setup_logging
from .session import setup_session

log = logging.getLogger(__name__)


ProgressCallback = Callable[[str], None]


def run_furrification(
    config: FurrifierConfig,
    load_order: Optional[LoadOrder] = None,
    progress: Optional[ProgressCallback] = None,
    cache: "Optional[SessionCache]" = None,
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
    cache : SessionCache, optional
        When present, reuse a previously-loaded plugin set / session
        instead of loading from scratch. The GUI passes the same cache
        instance its preview worker uses, so a preview run followed by
        a full Run pays the ~15s load cost once total.

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
        return _run_furrification_body(
            config, load_order, progress, log_counter, cache=cache)
    finally:
        root_logger.removeHandler(log_counter)


def _run_furrification_body(
    config: FurrifierConfig,
    load_order: Optional[LoadOrder],
    progress: Optional[ProgressCallback],
    log_counter: "_LogCounter",
    cache: "Optional[SessionCache]" = None,
) -> int:
    def emit(phase: str) -> None:
        if progress is not None:
            progress(phase)

    log.info("Skyrim Furrifier v0.1.0")
    log.info(f"  Scheme: {config.race_scheme}")
    log.info(f"  Patch: {config.patch_filename}")
    log.info(f"  Armor: {config.furrify_armor}")
    log.info(f"  Schlongs: {config.furrify_schlongs}")

    # Everything up through race/flst/preset furrification is the
    # same for full runs and for live preview. setup_session owns it.
    try:
        session = setup_session(config, load_order=load_order,
                                progress=progress, cache=cache)
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    plugin_set = session.plugin_set
    furry = session.context
    patch = session.patch
    ctx = furry.ctx

    # Furrify NPCs
    emit("Furrifying NPCs")
    log.info("Furrifying NPCs...")
    npc_count = furry.furrify_all_npcs(plugin_set, only_npc=config.only_npc)
    log.info(f"Furrified {npc_count} NPCs")

    # Extend leveled NPC lists with furry duplicates. --only mode skips
    # this — leveled-list extension creates NPC duplicates across the
    # whole load order, which defeats the "one NPC for visual diffing"
    # purpose of --only.
    if ctx.leveled_npc_groups and config.only_npc is None:
        log.info("Extending leveled NPC lists...")
        new_count, list_count = furry.extend_leveled_npcs(plugin_set)
        log.info(
            f"Created {new_count} leveled-list NPCs across {list_count} lists")

    # Furrify armor (skipped under --only — armor is a load-order-wide
    # transform unrelated to a single NPC's facegen).
    if config.furrify_armor and config.only_npc is None:
        emit("Merging armor overrides")
        log.info("Merging armor overrides...")
        merge_count = furry.merge_armor_overrides(plugin_set)
        log.info(f"Merged {merge_count} ARMO records")

        emit("Furrifying armor")
        log.info("Furrifying armor...")
        armor_count = furry.furrify_all_armor(plugin_set)
        log.info(f"Modified {armor_count} armor records")

    # Furrify schlongs (skipped under --only).
    if config.furrify_schlongs and config.only_npc is None:
        from .schlongs import furrify_all_schlongs
        emit("Furrifying schlongs")
        log.info("Furrifying schlongs...")
        race_assignments = {a.vanilla_id: a.furry_id
                            for a in ctx.assignments.values()}
        furry_to_vanilla: dict[str, list[str]] = {}
        for a in ctx.assignments.values():
            furry_to_vanilla.setdefault(a.furry_id, []).append(a.vanilla_id)
        for sub in ctx.subraces.values():
            race_assignments[sub.name] = sub.furry_id
            furry_to_vanilla.setdefault(sub.furry_id, []).append(sub.name)
        furrify_all_schlongs(plugin_set, patch, race_assignments,
                             furry_to_vanilla, furry.races)

    # Print statistics
    furry.print_statistics()

    # Save the patch first — the FaceGen engine reads nothing from it,
    # and saving before the (comparatively slow) FaceGen step means the
    # user always has a usable patch even if they Ctrl-C during bake.
    emit("Saving patch")
    patch.save()
    log.info(f"Saved patch: {patch.file_path}")

    # Build per-NPC FaceGenData (nif + DDS) under <output>/FaceGenData/
    # so the user doesn't have to open CK and Ctrl-F4. Source assets
    # (headpart nifs, tri files, tint masks) are resolved against
    # data_dir; the outputs land under output_dir.
    if config.build_facegen:
        emit("Building FaceGen")
        log.info("Building FaceGen...")
        _run_facegen(config, patch, plugin_set,
                     session.data_dir, session.output_dir, progress)

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
                                progress=progress,
                                limit=config.facegen_limit,
                                facetint_size=config.facetint_size,
                                only_npc=config.only_npc)

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
