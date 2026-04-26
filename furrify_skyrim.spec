# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the furrify_skyrim kit.
#
# Usage (from the furrifier/ project root):
#     pyinstaller furrify_skyrim.spec --noconfirm --clean
#
# Output:
#     dist/furrify_skyrim/furrify_skyrim.exe       — CLI entry point (console)
#     dist/furrify_skyrim/furrify_skyrim_gui.exe   — GUI entry point (windowed)
#     dist/furrify_skyrim/_internal/               — shared Python runtime + packed modules
#     dist/furrify_skyrim/schemes/                 — race scheme TOMLs (user-editable)
#     dist/furrify_skyrim/races/                   — race catalog TOMLs (user-editable)
#     dist/furrify_skyrim/README.md                — user docs for the TOML files
#
# Ship by zipping the entire dist/furrify_skyrim/ folder.
#
# Notes:
#   - Entry points are launcher.py (CLI) and launcher_gui.py (GUI), both at
#     the project root. They live outside src/furrifier/ because PyInstaller
#     runs them as plain scripts, not as `python -m`, so relative imports
#     don't work.
#   - Both exes share a single COLLECT (the `_internal/` folder) via
#     PyInstaller's two-EXE-one-COLLECT pattern. This keeps the kit
#     single-folder and avoids duplicating ~40MB of Python runtime.
#   - furrifier and esplib are pure-Python and get packed into each PYZ
#     archive inside the corresponding exe, so they won't appear as loose
#     files in _internal/. That's expected — don't mistake it for a broken
#     build.
#   - schemes/*.toml and races/*.toml are copied LOOSE next to the exe by
#     the post-build block at the bottom of this file — NOT via PyInstaller's
#     `datas`. They must stay loose and editable; bundling them via `datas`
#     would put them inside _internal/ where users can't find them.
#   - Game data is found at runtime via the Windows registry, so no
#     data-dir bundling is required.


# --- CLI exe (console) ---------------------------------------------------

a_cli = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[('src/furrifier/assets/*.png', 'furrifier/assets'),
           ('src/furrifier/assets/*.ico', 'furrifier/assets'),
           ('src/furrifier/assets/*.svg', 'furrifier/assets'),
           ('src/furrifier/preview/scene.qml', 'furrifier/preview'),
           ('src/furrifier/facegen/_bc7enc.dll', 'furrifier/facegen')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_cli = PYZ(a_cli.pure)

exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name='furrify_skyrim',
    icon='furrifier.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- GUI exe (windowed) --------------------------------------------------

a_gui = Analysis(
    ['launcher_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('src/furrifier/assets/*.png', 'furrifier/assets'),
           ('src/furrifier/assets/*.ico', 'furrifier/assets'),
           ('src/furrifier/assets/*.svg', 'furrifier/assets'),
           ('src/furrifier/preview/scene.qml', 'furrifier/preview'),
           ('src/furrifier/facegen/_bc7enc.dll', 'furrifier/facegen')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz_gui = PYZ(a_gui.pure)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name='furrify_skyrim_gui',
    icon='furrifier.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# --- Shared COLLECT (_internal/) -----------------------------------------
#
# Both exes get dropped into the same folder, sharing one _internal/ with
# the Python runtime and all packed binaries/datas merged from both
# Analyses.

coll = COLLECT(
    exe_cli,
    exe_gui,
    a_cli.binaries + a_gui.binaries,
    a_cli.datas + a_gui.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='furrify_skyrim',
)

# --- Post-build: copy schemes/ and races/ loose next to the exes ---------
#
# We deliberately avoid PyInstaller's `datas=` mechanism here, because
# that would tuck the files inside _internal/ where end users can't find
# or edit them. Both directories must live as siblings of the exes so a
# user can open dist/furrify_skyrim/schemes/user.toml (or races/user_races.toml)
# and tweak it. The furrifier's load_scheme uses sys.frozen detection to
# find them at runtime.
import shutil
from pathlib import Path

_spec_dir = Path(SPECPATH)
_dist_dir = Path(DISTPATH) / coll.name

# Test-only scheme files — frozen fixtures for the test suite, not
# shipped in the kit.
_TEST_ONLY = {'all_races_test.toml', 'ungulate_test.toml'}


def _ignore_test_files(dirname, names):
    return [n for n in names if n in _TEST_ONLY]


for _folder_name in ('schemes', 'races'):
    _src = _spec_dir / _folder_name
    _dst = _dist_dir / _folder_name
    if _src.is_dir():
        shutil.copytree(_src, _dst, dirs_exist_ok=True,
                        ignore=_ignore_test_files)
        print(f"Copied {_src} -> {_dst}")
    else:
        print(f"WARNING: {_folder_name}/ directory not found at {_src}")

# User-facing docs for the TOML files. Ships loose next to the exes so
# users who unzip the release see the README without digging into the
# source repo.
_readme_src = _spec_dir / 'README.md'
_readme_dst = _dist_dir / 'README.md'
if _readme_src.is_file():
    shutil.copyfile(_readme_src, _readme_dst)
    print(f"Copied {_readme_src} -> {_readme_dst}")
else:
    print(f"WARNING: README.md not found at {_readme_src}")
