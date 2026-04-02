"""Furrifier main entry point.

Loads plugins, applies race preference scheme, furrifies NPCs and armor,
saves patch file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from esplib import Plugin, PluginSet, LoadOrder, find_game

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

    log.info(f"Furrifier v0.1.0 -- scheme: {config.race_scheme}")

    # Find Skyrim
    game = find_game('tes5')
    if game is None and config.game_data_dir:
        data_dir = Path(config.game_data_dir)
    elif game is not None:
        data_dir = game.data_dir
    else:
        log.error("Could not find Skyrim installation. Use --data-dir.")
        return 1

    log.info(f"Data directory: {data_dir}")

    # Load preference scheme
    ctx = load_scheme(config.race_scheme)
    setup_vanilla(ctx)

    # Load plugins (exclude the patch file itself from the load order)
    log.info("Loading plugins...")
    patch_name = config.patch_filename.lower()
    load_order = LoadOrder.from_list(
        [p.name for p in data_dir.glob('*.es[mp]')
         if p.name.lower() != patch_name],
        data_dir=data_dir,
        game_id='tes5',
    )
    plugin_set = PluginSet(load_order)
    plugin_set.load_all()
    log.info(f"Loaded {len(plugin_set)} plugins")

    # Load race and headpart data
    races_by_edid = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid.items()}
    headparts = load_headparts(plugin_set, ctx)

    # Build race headpart and tint indices
    race_headparts = build_race_headparts(list(plugin_set), headparts)
    race_tints = build_race_tints(list(plugin_set))

    # Create patch
    patch_path = data_dir / config.patch_filename
    masters = [p.file_path.name for p in plugin_set if p.file_path]
    patch = Plugin.new_plugin(patch_path, masters=masters[:254])

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
        log.info("Merging armor overrides (addons + keywords)...")
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
        furrify_all_schlongs(plugin_set, patch, race_assignments,
                             furry_to_vanilla, races)

    # Print statistics
    furry.print_statistics()

    # Save
    patch.save()
    log.info(f"Saved patch: {patch_path}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
