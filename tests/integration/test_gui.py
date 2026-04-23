"""Smoke/functional tests for the PySide6 GUI port.

Deliberately light — rendering and event-handling are a hassle to
test and Hugh's rule is "favor functionality tests over unit tests,
UIs especially". These tests cover:

- The module imports cleanly (catches any stale references from
  the customtkinter version).
- Pure helpers (_read_plugin_masters) still work.
- The config-from-widget-state path produces a valid FurrifierConfig
  reflecting the widget values — this is the load-bearing glue layer
  between Qt and the furrifier core.

Widget tests need a QApplication; pytest-qt isn't a dep so we roll
our own fixture.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def qapp():
    """A singleton QApplication for the session. Must exist before
    any QWidget is instantiated."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_gui_module_imports():
    """Catches broken imports in the port (dead references to
    customtkinter, missing PySide6 classes, etc.)."""
    from furrifier import gui
    assert hasattr(gui, "FurrifierWindow")
    assert hasattr(gui, "PluginPickerDialog")
    assert hasattr(gui, "main")


def test_config_from_fields_default_values(qapp):
    """Building FurrifierConfig from a freshly-constructed window
    should produce the default-off-nothing-selected config the user
    would see before touching any widget."""
    from furrifier.gui import FurrifierWindow

    window = FurrifierWindow()
    try:
        config = window._config_from_fields()
        assert config.race_scheme == "all_races"
        assert config.patch_filename == "YASNPCPatch.esp"
        assert config.furrify_armor is True
        assert config.furrify_schlongs is True
        assert config.build_facegen is True
        assert config.debug is False
        assert config.log_file is None
        assert config.profile_file is None
        assert config.output_dir is None
    finally:
        window.deleteLater()


def test_config_from_fields_reflects_widget_state(qapp):
    """Changing widget values must flow through into the config."""
    from furrifier.gui import FurrifierWindow

    window = FurrifierWindow()
    try:
        window.patch_edit.setText("CustomPatch")  # no extension
        window.scheme_combo.setCurrentText("cats_dogs")
        window.armor_cb.setChecked(False)
        window.debug_cb.setChecked(True)
        window.log_file_edit.setText("c:/tmp/foo.log")
        window.data_dir_edit.setText("c:/game/Data")
        window.output_dir_edit.setText("c:/mods/sandbox")

        config = window._config_from_fields()
        # Patch gets .esp appended when the user leaves the extension off.
        assert config.patch_filename == "CustomPatch.esp"
        assert config.race_scheme == "cats_dogs"
        assert config.furrify_armor is False
        assert config.debug is True
        assert config.log_file == "c:/tmp/foo.log"
        assert config.game_data_dir == "c:/game/Data"
        assert config.output_dir == "c:/mods/sandbox"
    finally:
        window.deleteLater()


def test_config_from_fields_facegen_limit(qapp):
    """The FaceGen limit field: blank → None (no cap); integer → int."""
    from furrifier.gui import FurrifierWindow

    window = FurrifierWindow()
    try:
        # Default: blank → no limit.
        assert window._config_from_fields().facegen_limit is None
        # Typed in → int.
        window.facegen_limit_edit.setText("25")
        assert window._config_from_fields().facegen_limit == 25
        # Cleared again → None.
        window.facegen_limit_edit.setText("")
        assert window._config_from_fields().facegen_limit is None
    finally:
        window.deleteLater()


def test_config_from_fields_facetint_size(qapp):
    """Tint-size combo: Auto → None; explicit size → int."""
    from furrifier.gui import FurrifierWindow

    window = FurrifierWindow()
    try:
        # Default: "Auto" → None (compositor picks native mask size).
        assert window._config_from_fields().facetint_size is None
        # Select 1024.
        idx = window.facetint_size_combo.findData(1024)
        assert idx >= 0, "1024 must be an option in the tint-size combo"
        window.facetint_size_combo.setCurrentIndex(idx)
        assert window._config_from_fields().facetint_size == 1024
        # All five power-of-two sizes are selectable.
        for size in (256, 512, 1024, 2048, 4096):
            assert window.facetint_size_combo.findData(size) >= 0, (
                f"{size} must be selectable")
    finally:
        window.deleteLater()


def test_read_plugin_masters_handles_missing_file(tmp_path):
    """The helper must never raise — callers treat its output as a
    best-effort hint, not a contract."""
    from furrifier.gui import _read_plugin_masters
    result = _read_plugin_masters(tmp_path / "does_not_exist.esp")
    assert result == []


def test_read_plugin_masters_on_real_plugin():
    """Against a real plugin with a known master list."""
    from pathlib import Path
    from furrifier.gui import _read_plugin_masters
    # Skyrim's Update.esm is very stable and widely present. If this
    # file isn't on the dev machine we skip rather than fail.
    path = Path(r"C:\Steam\steamapps\common\Skyrim Special Edition"
                r"\Data\Update.esm")
    if not path.is_file():
        pytest.skip(f"{path} not available")
    masters = _read_plugin_masters(path)
    # Update.esm lists Skyrim.esm as its single master.
    assert any(m.lower() == "skyrim.esm" for m in masters)
