"""Tests for the copy-pasteable command line logged before each run.

The line has to round-trip: whatever it prints must parse back into the
same config, or it silently misdescribes the run it claims to reproduce.
"""

import shlex

import pytest

from furrifier.config import (
    FurrifierConfig, build_parser, command_line, normalize_argv,
)

PROG = "furrify_skyrim.exe"


def _roundtrip(config: FurrifierConfig) -> FurrifierConfig:
    """Render the config to a command line, parse it back."""
    line = command_line(config, program=PROG)
    argv = shlex.split(line[len(PROG):].strip(), posix=False)
    # shlex keeps the quotes in non-posix mode; strip them the way cmd
    # would before argparse ever sees the value.
    argv = [a[1:-1].replace('""', '"') if a.startswith('"') else a
            for a in argv]
    args = build_parser().parse_args(normalize_argv(argv))
    return FurrifierConfig.from_args(args)


def test_default_config_is_a_bare_command():
    assert command_line(FurrifierConfig(), program=PROG) == PROG


def test_only_non_defaults_appear():
    cfg = FurrifierConfig(facetint_size=1024)
    assert command_line(cfg, program=PROG) == f"{PROG} --facetint-size 1024"


def test_paths_with_spaces_are_quoted():
    cfg = FurrifierConfig(
        game_data_dir=r"C:\Steam\steamapps\common\Skyrim Special Edition\Data",
        output_dir=r"C:\Vortex\YAS NPCs Delivered")
    line = command_line(cfg, program=PROG)
    assert '--data-dir "C:\\Steam\\steamapps\\common\\Skyrim Special Edition\\Data"' in line
    assert '-o "C:\\Vortex\\YAS NPCs Delivered"' in line


def test_unspaced_path_is_not_quoted():
    cfg = FurrifierConfig(output_dir=r"C:\out")
    assert command_line(cfg, program=PROG) == rf"{PROG} -o C:\out"


def test_negative_flags():
    cfg = FurrifierConfig(furrify_armor=False, furrify_schlongs=False,
                          build_facegen=False)
    line = command_line(cfg, program=PROG)
    assert "--no-armor" in line
    assert "--no-schlongs" in line
    assert "--no-facegen" in line


def test_throttle_suppresses_workers():
    """--throttle overrides --workers; emitting both would describe a
    run that never happened."""
    cfg = FurrifierConfig(facegen_throttle=True, facegen_workers=8)
    line = command_line(cfg, program=PROG)
    assert "--throttle" in line
    assert "--workers" not in line


def test_workers_alone_is_emitted():
    cfg = FurrifierConfig(facegen_workers=4)
    assert command_line(cfg, program=PROG) == f"{PROG} --workers 4"


def test_derived_log_path_is_suppressed(tmp_path):
    """setup_logging writes the resolved path back onto the config. If we
    echoed it, a pasted command with a different -o would keep logging to
    the old run's folder."""
    out = tmp_path / "out"
    out.mkdir()
    cfg = FurrifierConfig(output_dir=str(out),
                          log_file=str(out / "furrify.log"))
    assert "--log" not in command_line(cfg, program=PROG)


def test_explicit_log_path_is_kept(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    cfg = FurrifierConfig(output_dir=str(out),
                          log_file=str(tmp_path / "elsewhere.log"))
    assert "--log" in command_line(cfg, program=PROG)


@pytest.mark.parametrize("cfg", [
    FurrifierConfig(),
    FurrifierConfig(race_scheme="cats_dogs", patch_filename="Test.esp"),
    FurrifierConfig(facetint_size=2048, facegen_limit=50, debug=True),
    FurrifierConfig(furrify_armor=False, preserve_existing=True),
    FurrifierConfig(only_npc="LeifWayfinder", facegen_workers=2),
    FurrifierConfig(
        game_data_dir=r"C:\Steam\steamapps\common\Skyrim Special Edition\Data",
        output_dir=r"C:\Users\me\AppData\Roaming\Vortex\skyrimse\mods\YAS NPCs"),
    FurrifierConfig(facegen_throttle=True, log_file="run one.log"),
])
def test_roundtrips(cfg):
    """Every rendered line must parse back to the config it came from."""
    assert _roundtrip(cfg) == cfg
