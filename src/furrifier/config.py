"""Configuration and CLI argument parsing.

Ported from BDFurrySkyrimOptions.pas.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .race_defs import list_available_schemes


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

    # Square edge length (pixels) for baked face-tint DDS output.
    # Must be one of facegen.composite.VALID_OUTPUT_SIZES or None.
    # None = match the first resolvable mask's native size (vanilla = 512).
    facetint_size: Optional[int] = None

    # Bake exactly one NPC, matched by EditorID (case-insensitive) or
    # hex form-id object index. Skips leveled-list extension, armor, and
    # schlongs so a single-NPC export takes seconds, not minutes. Used
    # for visual diffing against a CK-baked reference.
    only_npc: Optional[str] = None

    # When True, skip NPCs whose winning override is already a furrifier
    # output (RNAM points at a scheme target race). Use to extend a
    # curated patch with new mods' NPCs without re-deriving the existing
    # ones. See PLAN_FURRIFIER_REFURRIFY.md.
    preserve_existing: bool = False

    # FaceGen worker pool size. None = auto (cpu_count-1, capped at 8).
    # Set explicitly via --workers or FURRIFY_FACEGEN_WORKERS env for
    # benchmarking; 1 reproduces the pre-parallel serial path.
    facegen_workers: Optional[int] = None

    # When True, cap facegen workers to 1 and demote them to
    # BELOW_NORMAL priority on Windows. For users who want to leave a
    # bake running while doing other work — costs back the parallelism
    # speedup, but keeps the foreground responsive.
    facegen_throttle: bool = False

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
            facetint_size=args.facetint_size,
            only_npc=args.only_npc,
            preserve_existing=args.preserve_existing,
            facegen_workers=args.facegen_workers,
            facegen_throttle=args.facegen_throttle,
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
    scheme_kwargs = {}
    discovered = list_available_schemes()
    if discovered:
        scheme_kwargs['choices'] = discovered
    parser.add_argument('--scheme', default='all_races',
                        type=str.lower,
                        help='Race assignment scheme (default: all_races). '
                             'Any *.toml file in schemes/ is selectable.',
                        **scheme_kwargs)
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
    parser.add_argument('-o', '--output', dest='output_dir', metavar='DIR',
                        help='Directory to WRITE the patch and FaceGenData '
                             'into (defaults to --data-dir; set to a mod '
                             "manager's staging folder to keep Data clean)")
    parser.add_argument('--output-dir', dest='output_dir',
                        help=argparse.SUPPRESS)
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging')
    parser.add_argument('--log', dest='log_file', metavar='FILE',
                        help='Write log to file')
    parser.add_argument('--log-file', dest='log_file',
                        help=argparse.SUPPRESS)
    parser.add_argument('--profile', metavar='PATH',
                        help='Run under cProfile and dump stats to PATH. '
                             'Inspect with snakeviz or pstats.')
    parser.add_argument('--limit', dest='facegen_limit', type=int, metavar='N',
                        help='Cap FaceGen to the first N NPCs. Useful for '
                             'previewing a scheme without a full bake.')
    parser.add_argument('--facetint-size', type=int,
                        choices=(256, 512, 1024, 2048, 4096),
                        help='Square edge length (pixels) for baked face-tint '
                             'DDS output. Default: match first mask\'s native '
                             'size (vanilla = 512).')
    parser.add_argument('--only', dest='only_npc', metavar='EDID_OR_FID',
                        help='Bake exactly one NPC (matched by EditorID '
                             "case-insensitively, or hex form-id object "
                             'index). Skips armor, schlongs, and leveled-'
                             'list extension; useful for visual diffing.')
    parser.add_argument('--preserve-existing', action='store_true',
                        help='Skip NPCs whose winning override is already '
                             'furrified (RNAM points at a scheme target '
                             'race). Default: re-derive from the topmost '
                             'non-furry override to pick up scheme/'
                             'classifier fixes.')
    parser.add_argument('--workers', dest='facegen_workers', type=int,
                        metavar='N',
                        help='FaceGen worker process count. Default: '
                             'cpu_count-1, capped at 8. Set to 1 for the '
                             'serial baseline; >1 for parallel bake. '
                             'Overridden by --throttle. Also honored via '
                             'FURRIFY_FACEGEN_WORKERS env var.')
    parser.add_argument('--throttle', dest='facegen_throttle',
                        action='store_true',
                        help='Cap FaceGen at one BELOW_NORMAL-priority '
                             'worker so the machine stays responsive. '
                             'Wall-time matches the serial path; intended '
                             'for "leave it running" overnight bakes.')
    return parser


def resolve_log_path(config: FurrifierConfig) -> Optional[Path]:
    """Absolute log path for `config`, applying:
    - log_file unset → <output_dir>/furrify.log
    - log_file is a bare filename → <output_dir>/<filename>
    - log_file has a directory (absolute or relative) → honored as-is
    - log_file without a suffix → ".log" appended

    Output directory resolves output_dir → game_data_dir → esplib's
    find_game_data. Returns None if log_file is unset and no output
    dir resolves; returns the bare path unchanged if we have a name
    but no directory to anchor it."""
    def with_log_suffix(p: Path) -> Path:
        return p if p.suffix else p.with_suffix(".log")

    user = Path(config.log_file) if config.log_file else None
    if user is not None and (user.is_absolute() or user.parent != Path('.')):
        return with_log_suffix(user)
    try:
        if config.output_dir:
            target: Optional[Path] = Path(config.output_dir)
        elif config.game_data_dir:
            target = Path(config.game_data_dir)
        else:
            from esplib import find_game_data
            target = find_game_data("tes5")
    except Exception:
        target = None
    if target is None:
        return with_log_suffix(user) if user is not None else None
    name = user.name if user is not None else "furrify.log"
    return with_log_suffix(target / name)


def setup_logging(config: FurrifierConfig) -> None:
    resolved = resolve_log_path(config)
    if resolved is not None:
        config.log_file = str(resolved)
    level = logging.DEBUG if config.debug else logging.INFO
    handlers = [logging.StreamHandler()]
    if config.log_file:
        handlers.append(logging.FileHandler(config.log_file))
    # force=True wins over pynifly's import-time logging.basicConfig
    # (pyn/niflytools.py:17 sets level=DEBUG before we get here). Without
    # force, our basicConfig is a no-op because the root logger already
    # has handlers, and pynifly's chatty DEBUG output spills to stderr.
    logging.basicConfig(
        level=level,
        format='%(levelname)s: %(message)s',
        handlers=handlers,
        force=True,
    )
    # pynifly's "Reading tris from..." debug stream is interesting for
    # debugging but unwanted at INFO. Pin its logger at WARNING unless
    # the user explicitly asked for debug.
    if not config.debug:
        logging.getLogger("pynifly").setLevel(logging.WARNING)
