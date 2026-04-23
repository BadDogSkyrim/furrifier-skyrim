"""NifSkope integration for the live-preview pane.

Users with NifSkope installed get a one-click handoff from the
embedded preview to the canonical NIF renderer. Auto-detects common
install locations; falls back to a file picker + remembers the
choice via QSettings for the next session.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings


log = logging.getLogger("furrifier.preview.nifskope")


_ORG = "BadDogSkyrim"
_APP = "Furrifier"
_SETTINGS_KEY = "nifskope/path"


def _candidate_paths() -> list[Path]:
    """Common install locations, ordered by likelihood."""
    home = Path(os.environ.get("USERPROFILE", "C:/"))
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    program_files_x86 = Path(os.environ.get(
        "ProgramFiles(x86)", r"C:\Program Files (x86)"))
    candidates = [
        program_files / "NifSkope" / "NifSkope.exe",
        program_files_x86 / "NifSkope" / "NifSkope.exe",
        home / "NifSkope" / "NifSkope.exe",
        home / "Documents" / "NifSkope" / "NifSkope.exe",
        # Hugh's convention for modding tools.
        Path(r"C:\Modding\NifSkope\NifSkope.exe"),
        Path(r"C:\Modding\Tools\NifSkope\NifSkope.exe"),
    ]
    return candidates


def remember_path(path: Path) -> None:
    """Persist the NifSkope exe path so the next session finds it
    without asking again."""
    QSettings(_ORG, _APP).setValue(_SETTINGS_KEY, str(path))


def saved_path() -> Optional[Path]:
    """Return the path the user picked last time, if any."""
    value = QSettings(_ORG, _APP).value(_SETTINGS_KEY)
    if not value:
        return None
    p = Path(value)
    return p if p.is_file() else None


def find_nifskope() -> Optional[Path]:
    """Best-effort auto-detect. Returns the first exe that exists
    from (saved preference, then common install paths). None if none
    found — caller should fall back to a file picker."""
    saved = saved_path()
    if saved is not None:
        return saved
    for candidate in _candidate_paths():
        if candidate.is_file():
            remember_path(candidate)
            return candidate
    return None


def launch(exe: Path, nif_path: Path) -> None:
    """Spawn NifSkope pointed at `nif_path`. Non-blocking; the GUI
    stays responsive while the viewer window opens independently."""
    subprocess.Popen(
        [str(exe), str(nif_path)],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
