"""Tests for `expand_load_order_with_masters` — the first-pass that
pulls in any master plugins the user didn't explicitly select.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from esplib import LoadOrder, Plugin

from furrifier.session import (
    expand_load_order_with_masters,
    read_plugin_masters,
)


def _make_plugin(path: Path, masters: list[str]) -> None:
    """Write a minimal valid TES4 plugin at ``path`` with ``masters``."""
    plugin = Plugin.new_plugin(path, masters=masters, game="tes5")
    plugin.save()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path


def test_read_plugin_masters_returns_declared_masters(data_dir):
    _make_plugin(data_dir / "Dep.esp", masters=["A.esm", "B.esm"])
    assert read_plugin_masters(data_dir / "Dep.esp") == ["A.esm", "B.esm"]


def test_read_plugin_masters_returns_empty_for_missing_file(data_dir):
    assert read_plugin_masters(data_dir / "missing.esp") == []


def test_expand_pulls_missing_master(data_dir):
    _make_plugin(data_dir / "A.esm", masters=[])
    _make_plugin(data_dir / "Dep.esp", masters=["A.esm"])
    lo = LoadOrder.from_list(["Dep.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 1
    assert lo.plugins == ["A.esm", "Dep.esp"]


def test_expand_is_noop_when_masters_already_present(data_dir):
    _make_plugin(data_dir / "A.esm", masters=[])
    _make_plugin(data_dir / "Dep.esp", masters=["A.esm"])
    lo = LoadOrder.from_list(
        ["A.esm", "Dep.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 0
    assert lo.plugins == ["A.esm", "Dep.esp"]


def test_expand_handles_transitive_masters(data_dir):
    _make_plugin(data_dir / "Base.esm", masters=[])
    _make_plugin(data_dir / "Mid.esm", masters=["Base.esm"])
    _make_plugin(data_dir / "Top.esp", masters=["Mid.esm"])
    lo = LoadOrder.from_list(["Top.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 2
    assert lo.plugins == ["Base.esm", "Mid.esm", "Top.esp"]


def test_expand_preserves_master_declaration_order(data_dir):
    """When a plugin declares masters [B, A], both are missing — both
    get inserted in declared order ahead of the dependent."""
    _make_plugin(data_dir / "A.esm", masters=[])
    _make_plugin(data_dir / "B.esm", masters=[])
    _make_plugin(data_dir / "Dep.esp", masters=["B.esm", "A.esm"])
    lo = LoadOrder.from_list(["Dep.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 2
    assert lo.plugins == ["B.esm", "A.esm", "Dep.esp"]


def test_expand_skips_plugins_missing_from_disk(data_dir):
    """A selected plugin that isn't on disk can't be read for masters;
    the function silently moves on without crashing."""
    lo = LoadOrder.from_list(
        ["Ghost.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 0
    assert lo.plugins == ["Ghost.esp"]


def test_expand_adds_missing_master_even_if_not_on_disk(data_dir):
    """When B is declared as a master but isn't on disk, we still
    insert it — matching the behavior of a stale plugins.txt entry.
    PluginSet will log MISSING when it tries to load it."""
    _make_plugin(data_dir / "Dep.esp", masters=["Phantom.esm"])
    lo = LoadOrder.from_list(["Dep.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 1
    assert lo.plugins == ["Phantom.esm", "Dep.esp"]


def test_expand_does_not_duplicate_shared_master(data_dir):
    """Two plugins share a master. Only one insertion."""
    _make_plugin(data_dir / "Shared.esm", masters=[])
    _make_plugin(data_dir / "A.esp", masters=["Shared.esm"])
    _make_plugin(data_dir / "B.esp", masters=["Shared.esm"])
    lo = LoadOrder.from_list(
        ["A.esp", "B.esp"], data_dir=data_dir, game_id="tes5")

    added = expand_load_order_with_masters(lo, data_dir)

    assert added == 1
    assert lo.plugins == ["Shared.esm", "A.esp", "B.esp"]
