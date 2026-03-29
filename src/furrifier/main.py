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
from .furry_load import load_races, load_headparts

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

    # Load plugins
    log.info("Loading plugins...")
    load_order = LoadOrder.from_list(
        [p.name for p in data_dir.glob('*.es[mp]')],
        data_dir=data_dir,
        game_id='tes5',
    )
    plugin_set = PluginSet(load_order)
    plugin_set.load_all()

    plugins = [plugin_set.get_plugin(name) for name in load_order]
    plugins = [p for p in plugins if p is not None]
    log.info(f"Loaded {len(plugins)} plugins")

    # Load race and headpart data
    races_by_edid = load_races(plugin_set, ctx)
    races = {edid: info.record for edid, info in races_by_edid.items()}
    headparts = load_headparts(plugin_set, ctx)

    # Build race headpart index
    race_headparts: dict = {}
    # TODO: build this index from the loaded data

    # Build race tint data
    race_tints: dict = {}
    # TODO: build this from race head data tint assets

    # Create patch
    patch_path = data_dir / config.patch_filename
    masters = [p.file_path.name for p in plugins if p.file_path]
    patch = Plugin.new_plugin(patch_path, masters=masters[:254])

    # Build context
    furry = FurryContext(
        patch=patch,
        ctx=ctx,
        races=races,
        all_headparts=headparts,
        race_headparts=race_headparts,
        race_tints=race_tints,
        max_tint_layers=config.max_tint_layers,
    )

    # Furrify NPCs
    if config.furrify_npcs_male or config.furrify_npcs_female:
        log.info("Furrifying NPCs...")
        npc_count = furry.furrify_all_npcs(
            plugins,
            furrify_male=config.furrify_npcs_male,
            furrify_female=config.furrify_npcs_female,
        )
        log.info(f"Furrified {npc_count} NPCs")

    # Furrify armor
    if config.furrify_armor:
        log.info("Furrifying armor...")
        armor_count = furry.furrify_all_armor(plugins)
        log.info(f"Modified {armor_count} armor records")

    # Furrify schlongs
    if config.furrify_schlongs:
        from .schlongs import furrify_all_schlongs
        log.info("Furrifying schlongs...")
        furrify_all_schlongs(plugins, patch, [])

    # Save
    patch.save()
    log.info(f"Saved patch: {patch_path}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
