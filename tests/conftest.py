"""Package test root -- see the prerequisites banner below."""

# --- test prerequisites banner ---------------------------------------
# Reports missing games/mods above the run. Imported rather than defined
# in the repo-root conftest because this package has its own
# pyproject.toml, which makes IT the pytest rootdir when its suite runs
# directly — and the root conftest is then never loaded.
import sys as _sys
from pathlib import Path as _Path

_REPO_ROOT = _Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_REPO_ROOT))
from test_prereqs import pytest_report_header  # noqa: E402,F401

# `tests/facegen/test_only_npc.py` does `from tests.facegen.test_facegen_limit
# import ...`, which needs the parent of `tests/` importable. With
# `python -m pytest` that happens only as a side effect of the current
# directory being furrifier/ — run the suite from the repo root and
# collection fails outright with ModuleNotFoundError: No module named
# 'tests'. Make it independent of where pytest was launched from.
_PKG_ROOT = _Path(__file__).resolve().parents[1]
if str(_PKG_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PKG_ROOT))
