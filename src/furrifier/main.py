"""Furrifier main entry point.

Loads plugins, applies race preference scheme, furrifies NPCs and armor,
saves patch file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from esplib import Plugin, PluginSet, LoadOrder, find_game_data, find_strings_dir

from .config import FurrifierConfig, build_parser, setup_logging
from .context import FurryContext
from .race_defs import load_scheme
from .vanilla_setup import setup_vanilla
from .furry_load import load_races, load_headparts, build_race_headparts, build_race_tints

log = logging.getLogger(__name__)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = FurrifierConfig.from_args(args)
    setup_logging(config)
    logging.getLogger().addHandler(_log_counter)

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

    log.debug(f"Data directory: {data_dir}")

    # Load preference scheme
    ctx = load_scheme(config.race_scheme)
    setup_vanilla(ctx)

    # Load active plugins from plugins.txt (exclude the patch itself)
    log.debug("Loading plugins...")
    patch_name = config.patch_filename.lower()
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
    races_by_edid = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid.items()}
    headparts = load_headparts(plugin_set, ctx)

    # Build race headpart and tint indices
    race_headparts = build_race_headparts(list(plugin_set), headparts)
    race_tints = build_race_tints(list(plugin_set))

    # Create patch (masters added lazily as FormIDs are remapped)
    patch_path = data_dir / config.patch_filename
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
    log.info("Furrifying races...")
    race_count = furry.furrify_all_races()
    log.info(f"Furrified {race_count} races")

    # Furrify headpart FormLists (must be after race furrification)
    log.info("Furrifying headpart lists...")
    flst_count = furry.furrify_all_headpart_lists(plugin_set)
    log.info(f"Modified {flst_count} headpart FormLists")

    # Furrify race presets (must be after race furrification)
    log.info("Furrifying race presets...")
    preset_count = furry.furrify_race_presets(plugin_set)
    log.info(f"Created {preset_count} race preset NPCs")

    # Furrify NPCs
    if config.furrify_npcs_male or config.furrify_npcs_female:
        log.info("Furrifying NPCs...")
        npc_count = furry.furrify_all_npcs(
            plugin_set,
            furrify_male=config.furrify_npcs_male,
            furrify_female=config.furrify_npcs_female,
        )
        log.info(f"Furrified {npc_count} NPCs")

    # Furrify armor
    if config.furrify_armor:
        log.info("Merging armor overrides...")
        merge_count = furry.merge_armor_overrides(plugin_set)
        log.info(f"Merged {merge_count} ARMO records")

        log.info("Furrifying armor...")
        armor_count = furry.furrify_all_armor(plugin_set)
        log.info(f"Modified {armor_count} armor records")

    # Furrify schlongs
    if config.furrify_schlongs:
        from .schlongs import furrify_all_schlongs
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

    # Print warning/error summary
    _print_log_summary()

    # Save
    patch.save()
    log.info(f"Saved patch: {patch_path}")

    return 0


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


_log_counter = _LogCounter()


def _print_log_summary():
    """Print summary of warnings and errors at end of run."""
    if _log_counter.warnings:
        print(f"\n{len(_log_counter.warnings)} warning(s):")
        for msg in _log_counter.warnings:
            print(f"  {msg}")
    if _log_counter.errors:
        print(f"\n{len(_log_counter.errors)} error(s):")
        for msg in _log_counter.errors:
            print(f"  {msg}")
    if not _log_counter.warnings and not _log_counter.errors:
        print("\nNo warnings or errors.")


if __name__ == '__main__':
    sys.exit(main())
