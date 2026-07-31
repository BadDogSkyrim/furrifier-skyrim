"""Logging setup must not be the thing that stops a run.

setup_logging runs before the session, which is what normally creates
the output directory — so a brand-new -o target used to crash on the
FileHandler open, with a raw traceback and no work done.
"""

import logging

import pytest

from furrifier.config import FurrifierConfig, setup_logging


@pytest.fixture(autouse=True)
def _restore_logging():
    yield
    for h in logging.root.handlers[:]:
        logging.root.removeHandler(h)
        h.close()


def _file_handlers():
    return [h for h in logging.root.handlers
            if isinstance(h, logging.FileHandler)]


def test_creates_a_missing_output_directory(tmp_path):
    out = tmp_path / "does" / "not" / "exist"
    setup_logging(FurrifierConfig(output_dir=str(out)))

    log_path = out / "furrify.log"
    assert log_path.parent.is_dir()
    logging.getLogger("furrifier").info("hello")
    for h in _file_handlers():
        h.flush()
    assert log_path.is_file()
    assert "hello" in log_path.read_text(encoding="utf-8", errors="replace")


def test_unwritable_log_falls_back_to_console(tmp_path, monkeypatch):
    """A log we can't open is not a reason to refuse to run."""
    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr("furrifier.config.logging.FileHandler", boom)
    out = tmp_path / "out"
    setup_logging(FurrifierConfig(output_dir=str(out)))
    # Undo before asserting — the patch replaces logging.FileHandler
    # globally, which would break the isinstance check below.
    monkeypatch.undo()

    assert not _file_handlers()
    assert logging.root.handlers, "console handler should still be installed"
