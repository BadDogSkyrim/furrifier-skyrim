"""Configuration and CLI argument parsing.

Ported from BDFurrySkyrimOptions.pas.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field, replace
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
    #
    # Also reads as "this is an additive pass over an existing furrifier
    # patch", and so suppresses the two passes that mint brand-new NPC
    # records — leveled-list extension and race chargen presets — which
    # would otherwise stack a second set of duplicates on top of the
    # ones the earlier patch already contributed.
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

    # Explicit plugin list (filenames, in load order) standing in for
    # the game's active load order. None = use plugins.txt. Mirrors the
    # GUI's plugin picker so a logged command line can reproduce a run
    # made against a hand-picked subset.
    plugin_selection: Optional[list] = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> FurrifierConfig:
        patch = args.patch or cls.patch_filename
        if Path(patch).suffix.lower() not in ('.esp', '.esm', '.esl'):
            patch += '.esp'
        plugins = None
        if args.plugins:
            plugins = [p.strip() for p in args.plugins.split(',') if p.strip()]
        return cls(
            patch_filename=patch,
            race_scheme=args.scheme or cls.race_scheme,
            furrify_armor=args.armor,
            furrify_schlongs=args.schlongs,
            build_facegen=args.facegen,
            plugin_selection=plugins,
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


def _add_toggle(parser: argparse.ArgumentParser, name: str, *,
                default: bool, on_help: str, off_help: str,
                dest: Optional[str] = None) -> None:
    """Add a `--x` / `--no-x` pair sharing one dest.

    Both spellings exist so `command_line()` can always name the state
    it chose. Inferring "armor is on" from the absence of `--no-armor`
    is exactly the ambiguity the logged line is supposed to remove.
    """
    dest = dest or name.replace('-', '_')
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f'--{name}', dest=dest, action='store_true',
                       default=default, help=on_help)
    group.add_argument(f'--no-{name}', dest=dest, action='store_false',
                       help=off_help)


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
    # Each toggle gets both spellings so a rendered command line can
    # state what it chose rather than leaving it to be inferred from an
    # absent flag. The `--no-*` forms are the originals; the positive
    # ones are new, so old batch files keep working.
    _add_toggle(parser, 'armor', default=True,
                on_help='Furrify armor (default)',
                off_help='Skip armor furrification')
    _add_toggle(parser, 'schlongs', default=True,
                on_help='Enable SOS (schlong) compatibility (default)',
                off_help='Disable SOS (schlong) compatibility')
    _add_toggle(parser, 'facegen', default=True,
                on_help='Build per-NPC FaceGen nif + DDS (default)',
                off_help='Skip building per-NPC FaceGen nif + DDS '
                         '(otherwise written alongside the patch under '
                         'FaceGenData/)')
    parser.add_argument('--plugins', metavar='LIST',
                        help='Comma-separated plugin filenames to load, in '
                             'load order. Default: the game\'s active load '
                             'order from plugins.txt. Mirrors the GUI\'s '
                             'plugin picker.')
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
    _add_toggle(parser, 'preserve-existing', default=False,
                dest='preserve_existing',
                on_help='Skip NPCs whose winning override is already '
                        'furrified (RNAM points at a scheme target race). '
                        'Also skips the passes that mint new NPC records '
                        '(leveled-list extension and race chargen presets), '
                        'since the patch being extended already has them.',
                off_help='Re-derive furrified NPCs from the topmost '
                         'non-furry override to pick up scheme/classifier '
                         'fixes (default).')
    parser.add_argument('--workers', dest='facegen_workers', type=int,
                        metavar='N',
                        help='FaceGen worker process count. Default: '
                             'cpu_count-1, capped at 8. Set to 1 for the '
                             'serial baseline; >1 for parallel bake. '
                             'Overridden by --throttle. Also honored via '
                             'FURRIFY_FACEGEN_WORKERS env var.')
    _add_toggle(parser, 'throttle', default=False, dest='facegen_throttle',
                on_help='Cap FaceGen at one BELOW_NORMAL-priority worker so '
                        'the machine stays responsive. Wall-time matches the '
                        'serial path; intended for "leave it running" '
                        'overnight bakes.',
                off_help='Use the normal FaceGen worker pool (default).')
    return parser


def _quote(value: str) -> str:
    """Quote an argument for a Windows batch file / cmd.exe.

    Not shlex.quote — that emits POSIX single quotes, which cmd.exe
    passes through literally and would break every path it touched.
    """
    text = str(value)
    if text and not any(c in text for c in ' \t"&|<>^()'):
        return text
    # cmd has no escape for a double quote inside a quoted string; the
    # convention that works for paths is to double it.
    return '"' + text.replace('"', '""') + '"'


def _program_name() -> str:
    """What to put at the front of the reproduction command line.

    A frozen run names the CLI exe rather than whatever is executing —
    the GUI exe is windowed and would swallow the console output the
    user is trying to capture. Both exes ship in the same folder.
    """
    if getattr(sys, 'frozen', False):
        return 'furrify_skyrim.exe'
    return 'python -m furrifier'


def _log_is_default(config: FurrifierConfig) -> bool:
    """Whether config.log_file is just the path the app would derive on
    its own (<output dir>/furrify.log).

    setup_logging writes the resolved absolute path back onto the
    config, so by the time anything renders a command line, log_file
    looks user-chosen even when it wasn't. Emitting it would pin the log
    to that exact path — so a pasted command with a different -o would
    keep writing its log to the *old* run's folder.
    """
    if not config.log_file:
        return False
    try:
        derived = resolve_log_path(replace(config, log_file=None))
    except Exception:
        return False
    if derived is None:
        return False
    return (os.path.normcase(os.path.abspath(config.log_file))
            == os.path.normcase(os.path.abspath(derived)))


def command_line(config: FurrifierConfig, program: Optional[str] = None) -> str:
    """Render `config` as a command line that reproduces this run.

    Only settings that differ from the parser's defaults are emitted, so
    the line stays readable and says what was actually chosen. Paths are
    always included when set — that's what makes it reproducible from a
    batch file in another directory.
    """
    argv: list[str] = [program or _program_name()]

    def flag(name: str, value) -> None:
        argv.extend((name, _quote(value)))

    defaults = FurrifierConfig()

    if config.race_scheme != defaults.race_scheme:
        flag('--scheme', config.race_scheme)
    if config.patch_filename != defaults.patch_filename:
        flag('--patch', config.patch_filename)
    if config.game_data_dir:
        flag('--data-dir', config.game_data_dir)
    if config.output_dir:
        flag('-o', config.output_dir)
    if config.plugin_selection:
        # An explicit picker selection. Omitted when None, which means
        # "the game's active load order" — the CLI default, and not
        # something a frozen list could honestly stand in for.
        flag('--plugins', ','.join(config.plugin_selection))

    # Toggles are always stated, on or off. Their whole reason for
    # being in the log is so nobody has to infer a setting from a flag
    # that isn't there.
    argv.append('--armor' if config.furrify_armor else '--no-armor')
    argv.append('--schlongs' if config.furrify_schlongs else '--no-schlongs')
    argv.append('--facegen' if config.build_facegen else '--no-facegen')
    argv.append('--preserve-existing' if config.preserve_existing
                else '--no-preserve-existing')
    argv.append('--throttle' if config.facegen_throttle else '--no-throttle')

    if config.facetint_size:
        flag('--facetint-size', config.facetint_size)
    if config.facegen_limit:
        flag('--limit', config.facegen_limit)
    if config.facegen_workers and not config.facegen_throttle:
        # --throttle overrides --workers; emitting both would misdescribe
        # the run it claims to reproduce.
        flag('--workers', config.facegen_workers)
    if config.only_npc:
        flag('--only', config.only_npc)
    if config.log_file and not _log_is_default(config):
        flag('--log', config.log_file)
    if config.profile_file:
        flag('--profile', config.profile_file)
    if config.debug:
        argv.append('--debug')

    return ' '.join(argv)


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
        # Create the log's directory first. Logging is set up before the
        # session, which is what normally mkdirs the output dir — so
        # pointing -o at a folder that doesn't exist yet used to die
        # here, on a FileHandler open, with a raw traceback and nothing
        # done. Falling back to console-only beats refusing to run.
        try:
            Path(config.log_file).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(config.log_file))
        except OSError as exc:
            print(f"warning: cannot write log to {config.log_file}: {exc}",
                  file=sys.stderr)
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
