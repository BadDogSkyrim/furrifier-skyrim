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
    furrify_schlongs: bool = True
    build_facegen: bool = True
    max_tint_layers: int = 200
    debug: bool = False
    log_file: Optional[str] = None
    # When set, wrap the run in cProfile and dump stats to this path.
    # Top 30 cumulative-time functions are also printed at the end.
    profile_file: Optional[str] = None
    # Cap the number of NPCs we build FaceGen for. None = no cap.
    # Useful for previewing a scheme's output without paying for a
    # full-load-order bake (minutes per run on 4000+ NPCs).
    facegen_limit: Optional[int] = None

    # Where to READ source assets (mods, masters, textures, BSAs).
    # Auto-detected via find_game_data() if not provided.
    game_data_dir: Optional[str] = None

    # Where to WRITE the patch + generated FaceGenData. Defaults to
    # game_data_dir. Separate when pointing at a mod-manager staging
    # folder (e.g. a Vortex/MO2 mod) so the build doesn't overwrite
    # files in the live Data tree.
    output_dir: Optional[str] = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> FurrifierConfig:
        patch = args.patch or cls.patch_filename
        if Path(patch).suffix.lower() not in ('.esp', '.esm', '.esl'):
            patch += '.esp'
        return cls(
            patch_filename=patch,
            race_scheme=args.scheme or cls.race_scheme,
            furrify_armor=not args.no_armor,
            furrify_schlongs=not args.no_schlongs,
            build_facegen=not args.no_facegen,
            debug=args.debug,
            log_file=args.log_file,
            game_data_dir=args.data_dir,
            output_dir=args.output_dir,
            profile_file=args.profile,
            facegen_limit=args.facegen_limit,
        )


# Need to import Optional for the type hint
from typing import Optional


def normalize_argv(argv: list[str]) -> list[str]:
    """Lowercase switch names (but not their values) so --DEBUG, --Debug,
    --debug all work. Values attached via = preserve case on the RHS."""
    out = []
    for tok in argv:
        if tok.startswith('-') and len(tok) > 1:
            if '=' in tok:
                flag, _, val = tok.partition('=')
                out.append(f"{flag.lower()}={val}")
            else:
                out.append(tok.lower())
        else:
            out.append(tok)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='furrify_skyrim',
        description='Batch-convert Skyrim NPCs to furry races using esplib.',
    )
    parser.add_argument('--patch', default='YASNPCPatch.esp',
                        help='Output patch filename (default: YASNPCPatch.esp)')
    parser.add_argument('--scheme', default='all_races',
                        type=str.lower,
                        choices=['all_races', 'cats_dogs', 'legacy', 'user'],
                        help='Race assignment scheme (default: all_races)')
    parser.add_argument('--no-armor', action='store_true',
                        help='Skip armor furrification')
    parser.add_argument('--no-schlongs', action='store_true',
                        help='Disable SOS (schlong) compatibility')
    parser.add_argument('--no-facegen', action='store_true',
                        help='Skip building per-NPC FaceGen nif + DDS '
                             '(otherwise written alongside the patch under '
                             'FaceGenData/)')
    parser.add_argument('--data-dir',
                        help='Path to Skyrim Data directory for READING '
                             'source assets (auto-detected if omitted)')
    parser.add_argument('--output-dir',
                        help='Directory to WRITE the patch and FaceGenData '
                             'into (defaults to --data-dir; set to a mod '
                             "manager's staging folder to keep Data clean)")
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--log-file',
                        help='Write log to file')
    parser.add_argument('--profile', metavar='PATH',
                        help='Run under cProfile and dump stats to PATH. '
                             'Inspect with snakeviz or pstats.')
    parser.add_argument('--facegen-limit', type=int, metavar='N',
                        help='Cap FaceGen to the first N NPCs. Useful for '
                             'previewing a scheme without a full bake.')
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
