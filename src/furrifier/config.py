"""Configuration and CLI argument parsing.

Ported from BDFurrySkyrimOptions.pas.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FurrifierConfig:
    """All configuration settings for a furrification run."""
    patch_filename: str = 'YASNPCPatch.esp'
    race_scheme: str = 'all_races'
    furrify_armor: bool = True
    furrify_npcs_male: bool = True
    furrify_npcs_female: bool = True
    furrify_schlongs: bool = True
    max_tint_layers: int = 200
    debug: bool = False
    log_file: Optional[str] = None

    # Paths (auto-detected if not provided)
    game_data_dir: Optional[str] = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> FurrifierConfig:
        patch = args.patch or cls.patch_filename
        if Path(patch).suffix.lower() not in ('.esp', '.esm', '.esl'):
            patch += '.esp'
        return cls(
            patch_filename=patch,
            race_scheme=args.scheme or cls.race_scheme,
            furrify_armor=not args.no_armor,
            furrify_npcs_male=not args.no_male,
            furrify_npcs_female=not args.no_female,
            furrify_schlongs=not args.no_schlongs,
            debug=args.debug,
            log_file=args.log_file,
            game_data_dir=args.data_dir,
        )


# Need to import Optional for the type hint
from typing import Optional


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='furrify_skyrim',
        description='Batch-convert Skyrim NPCs to furry races using esplib.',
    )
    parser.add_argument('--patch', default='YASNPCPatch.esp',
                        help='Output patch filename (default: YASNPCPatch.esp)')
    parser.add_argument('--scheme', default='all_races',
                        choices=['all_races', 'cats_dogs', 'legacy', 'user'],
                        help='Race assignment scheme (default: all_races)')
    parser.add_argument('--no-armor', action='store_true',
                        help='Skip armor furrification')
    parser.add_argument('--no-male', action='store_true',
                        help='Skip male NPC furrification')
    parser.add_argument('--no-female', action='store_true',
                        help='Skip female NPC furrification')
    parser.add_argument('--no-schlongs', action='store_true',
                        help='Disable SOS (schlong) compatibility')
    parser.add_argument('--data-dir',
                        help='Path to Skyrim Data directory (auto-detected if omitted)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--log-file',
                        help='Write log to file')
    return parser


def setup_logging(config: FurrifierConfig) -> None:
    level = logging.DEBUG if config.debug else logging.INFO
    handlers = [logging.StreamHandler()]
    if config.log_file:
        handlers.append(logging.FileHandler(config.log_file))
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s',
        handlers=handlers,
    )
