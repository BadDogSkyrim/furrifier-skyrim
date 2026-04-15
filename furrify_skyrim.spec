# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for furrify_skyrim CLI.
#
# Usage (from the furrifier/ project root):
#     pyinstaller furrify_skyrim.spec --noconfirm --clean
#
# Output:
#     dist/furrify_skyrim/furrify_skyrim.exe  — the CLI entry point
#     dist/furrify_skyrim/_internal/          — Python runtime + packed modules
#     dist/furrify_skyrim/schemes/            — race scheme TOMLs (user-editable)
#     dist/furrify_skyrim/races/              — race catalog TOMLs (user-editable)
#     dist/furrify_skyrim/README.md           — user docs for the TOML files
#
# Ship by zipping the entire dist/furrify_skyrim/ folder.
#
# Notes:
#   - Entry point is launcher.py at the project root (NOT src/furrifier/
#     __main__.py — that uses relative imports and can't run standalone).
#   - furrifier and esplib are pure-Python and get packed into the PYZ
#     archive inside the exe, so they won't appear as loose files in
#     _internal/. That's expected — don't mistake it for a broken build.
#   - schemes/*.toml and races/*.toml are copied LOOSE next to the exe by
#     the post-build block at the bottom of this file — NOT via PyInstaller's
#     `datas`. They must stay loose and editable; bundling them via `datas`
#     would put them inside _internal/ where users can't find them.
#   - Game data is found at runtime via the Windows registry, so no
#     data-dir bundling is required.
#   - console=True because this is a CLI. Switch to False (and add
#     windowed=True) if/when the GUI dialog lands.


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
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
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='furrify_skyrim',
)

# --- Post-build: copy schemes/ and races/ loose next to the exe ----------
#
# We deliberately avoid PyInstaller's `datas=` mechanism here, because
# that would tuck the files inside _internal/ where end users can't find
# or edit them. Both directories must live as siblings of the exe so a
# user can open dist/furrify_skyrim/schemes/user.toml (or races/user_races.toml)
# and tweak it. The furrifier's load_scheme uses sys.frozen detection to
# find them at runtime.
import shutil
from pathlib import Path

_spec_dir = Path(SPECPATH)
_dist_dir = Path(DISTPATH) / coll.name

# Test-only scheme files — frozen fixtures for the test suite, not
# shipped in the kit.
_TEST_ONLY = {'all_races_test.toml'}


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

# User-facing docs for the TOML files. Ships loose next to the exe so
# users who unzip the release see the README without digging into the
# source repo.
_readme_src = _spec_dir / 'README.md'
_readme_dst = _dist_dir / 'README.md'
if _readme_src.is_file():
    shutil.copyfile(_readme_src, _readme_dst)
    print(f"Copied {_readme_src} -> {_readme_dst}")
else:
    print(f"WARNING: README.md not found at {_readme_src}")
