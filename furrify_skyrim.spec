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
#   - Building bumps build_number.json (checked in) and writes the
#     generated src/furrifier/_build_stamp.py (gitignored) that carries
#     the number into the kit. build_info only reads the stamp when
#     frozen, so a leftover stamp can't make a dev run claim a build
#     number.


# --- PyNifly bundling ----------------------------------------------------
#
# `pyn` is the PyNifly Python package — not a pip dependency, just a
# folder Hugh has alongside the furrifier checkout. We add its parent to
# pathex so PyInstaller's analyzer discovers `from pyn.pynifly import …`
# imports; ship NiflyDLL.dll (its native counterpart) at the install-mode
# location pyn/niflydll.py expects (one level above the pyn package); and
# ship pyn/../tri/trifile.py because facegen/morph.py loads it via
# importlib to bypass tri/__init__.py's bpy import.
PYNIFLY_ROOT = r'C:\Modding\PyNifly\io_scene_nifly'
NIFLY_DLL = r'C:\Modding\PyNifly\NiflyDLL\x64\Release\NiflyDLL.dll'

_PYNIFLY_DATAS = [
    (PYNIFLY_ROOT + r'\tri\trifile.py', 'tri'),
]
_PYNIFLY_BINARIES = [
    (NIFLY_DLL, '.'),
]


# --- PyNifly DLL/binding version check -----------------------------------
#
# NIFLY_DLL above is a build artifact of a separate Visual Studio project,
# and nothing rebuilds it when niflydll.py gains a new binding. Worse, the
# mismatch is invisible in dev: with PYNIFLY_DEV_ROOT set, niflydll.py
# loads the *Debug* DLL, so Hugh's runs pick up a fresh binary while the
# kit ships whatever Release build happens to be lying around.
#
# That shipped on 2026-08-09. PyNifly commit da4b63d (2026-08-07) added
# getNiIntegersExtraDataValues on both the C++ and Python sides, but the
# Release DLL was last compiled 2026-07-17, so the kit paired new bindings
# with an old binary. niflydll.py binds every signature at import time, so
# it wasn't a latent bug in some rare code path -- the GUI died on launch
# for every user, on `import pyn.pynifly`, before drawing a window.
#
# So: read the DLL's export table and confirm it satisfies every mandatory
# binding, and fail the build if it doesn't. Only column-0 `nifly.NAME.`
# bindings are mandatory; the indented ones live inside `try/except
# AttributeError` blocks in niflydll.py and are optional by construction.
import re as _re
import struct as _struct
from pathlib import Path as _Path


def _dll_exports(path):
    """Return the set of names in a PE file's export table."""
    d = open(path, 'rb').read()
    pe = _struct.unpack_from('<I', d, 0x3c)[0]
    if d[pe:pe + 4] != b'PE\0\0':
        raise SystemExit(f'not a PE file: {path}')
    nsec = _struct.unpack_from('<H', d, pe + 6)[0]
    optsz = _struct.unpack_from('<H', d, pe + 20)[0]
    opt = pe + 24
    # Data directory sits after the optional header, whose size depends on
    # PE32 (0x10b) vs PE32+ (0x20b); export table is directory entry 0.
    dirs = opt + (112 if _struct.unpack_from('<H', d, opt)[0] == 0x20b else 96)
    export_rva = _struct.unpack_from('<I', d, dirs)[0]

    sections = []
    for i in range(nsec):
        base = opt + optsz + i * 40
        vsize, vaddr, rawsize, rawptr = _struct.unpack_from('<IIII', d, base + 8)
        sections.append((vaddr, max(vsize, rawsize), rawptr))

    def offset_of(rva):
        for vaddr, size, rawptr in sections:
            if vaddr <= rva < vaddr + size:
                return rawptr + (rva - vaddr)
        raise SystemExit(f'RVA {rva:#x} outside every section in {path}')

    table = offset_of(export_rva)
    count = _struct.unpack_from('<I', d, table + 24)[0]
    names = offset_of(_struct.unpack_from('<I', d, table + 32)[0])
    out = set()
    for i in range(count):
        o = offset_of(_struct.unpack_from('<I', d, names + 4 * i)[0])
        out.add(d[o:d.index(b'\0', o)].decode('ascii', 'replace'))
    return out


def _check_nifly_dll(dll_path, binding_path):
    required = set(_re.findall(r'^nifly\.([A-Za-z_]\w*)\.',
                               _Path(binding_path).read_text(encoding='utf-8'),
                               _re.M))
    if not required:
        raise SystemExit(f'parsed no nifly bindings from {binding_path} -- '
                         'the binding style changed; fix this check')
    missing = sorted(required - _dll_exports(dll_path))
    if missing:
        raise SystemExit(
            f'\nNiflyDLL.dll is stale -- it is missing {len(missing)} of the '
            f'{len(required)} functions {_Path(binding_path).name} binds at '
            f'import time:\n'
            + ''.join(f'    {name}\n' for name in missing)
            + f'\n  DLL:      {dll_path}\n'
              f'  Bindings: {binding_path}\n\n'
              'Shipping this pairing crashes the kit on launch. Rebuild the '
              'DLL in Release x64:\n'
              '    msbuild NiflyDLL.vcxproj -p:Configuration=Release '
              '-p:Platform=x64\n'
              '(Debug builds do not count -- the kit ships Release.)\n')
    print(f'NiflyDLL.dll satisfies all {len(required)} required bindings.')


# Run it before Analysis, so a stale DLL fails in a second rather than
# after a couple of minutes of packing.
_check_nifly_dll(NIFLY_DLL, PYNIFLY_ROOT + r'\pyn\niflydll.py')


# --- Build number --------------------------------------------------------
#
# A plain integer, bumped on every build, reset to 0 when the version
# changes. Enough to pin down which kit someone is running, and nothing
# more. The counter is checked in (build_number.json); the generated
# stamp module it feeds is not.
import json as _json  # _re and _Path come from the DLL check above

_spec_root = _Path(SPECPATH)
_counter_path = _spec_root / 'build_number.json'

# Read __version__ without importing the package — importing would drag
# in numpy/PySide6 during the spec's own execution.
_init_src = (_spec_root / 'src' / 'furrifier' / '__init__.py').read_text(
    encoding='utf-8')
_m = _re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', _init_src, _re.M)
if not _m:
    raise SystemExit('could not parse __version__ from src/furrifier/__init__.py')
_version = _m.group(1)

try:
    _state = _json.loads(_counter_path.read_text(encoding='utf-8'))
except Exception:
    _state = {}

if _state.get('version') == _version:
    _build_no = int(_state.get('build', 0)) + 1
else:
    # New version line — start its build numbering over.
    _build_no = 0

# NOTE: the counter file is NOT written here. The stamp has to exist
# before Analysis, but a build that dies later (PyInstaller can't clear
# dist/ while the previous kit is still running, for one) would then
# have burned a number that no kit ever shipped under. The persist step
# lives in the post-build block at the bottom, after COLLECT succeeds.

(_spec_root / 'src' / 'furrifier' / '_build_stamp.py').write_text(
    '"""Generated by furrify_skyrim.spec at build time. Do not edit."""\n'
    f'BUILD = {_build_no}\n',
    encoding='utf-8')
print(f'Build number: v{_version} build {_build_no}')

_BUILD_HIDDEN = ['furrifier._build_stamp']


# --- CLI exe (console) ---------------------------------------------------

a_cli = Analysis(
    ['launcher.py'],
    pathex=[PYNIFLY_ROOT],
    binaries=_PYNIFLY_BINARIES,
    datas=[('src/furrifier/assets/*.png', 'furrifier/assets'),
           ('src/furrifier/assets/*.ico', 'furrifier/assets'),
           ('src/furrifier/assets/*.svg', 'furrifier/assets'),
           ('src/furrifier/preview/scene.qml', 'furrifier/preview'),
           ('src/furrifier/facegen/_bc7enc.dll', 'furrifier/facegen')]
          + _PYNIFLY_DATAS,
    hiddenimports=_BUILD_HIDDEN,
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
    pathex=[PYNIFLY_ROOT],
    binaries=_PYNIFLY_BINARIES,
    datas=[('src/furrifier/assets/*.png', 'furrifier/assets'),
           ('src/furrifier/assets/*.ico', 'furrifier/assets'),
           ('src/furrifier/assets/*.svg', 'furrifier/assets'),
           ('src/furrifier/preview/scene.qml', 'furrifier/preview'),
           ('src/furrifier/facegen/_bc7enc.dll', 'furrifier/facegen')]
          + _PYNIFLY_DATAS,
    hiddenimports=_BUILD_HIDDEN,
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

# Images referenced by README.md (e.g. the GUI screenshot at the top).
# Without these, markdown viewers in the unzipped kit show broken links.
_images_src = _spec_dir / 'images'
_images_dst = _dist_dir / 'images'
if _images_src.is_dir():
    shutil.copytree(_images_src, _images_dst, dirs_exist_ok=True)
    print(f"Copied {_images_src} -> {_images_dst}")

# --- Persist the build number (last, so only a real kit claims one) -----
#
# Deliberately the final step. Everything above can still fail — most
# commonly PyInstaller refusing to clear dist/ because the previously
# built kit is still running — and a number burned by a failed build
# would name a kit that doesn't exist.
_counter_path.write_text(
    _json.dumps({'version': _version, 'build': _build_no}, indent=2) + '\n',
    encoding='utf-8')
print(f"Recorded build number: v{_version} build {_build_no}")
