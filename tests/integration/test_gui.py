"""Smoke/functional tests for the PySide6 GUI port.

Deliberately light — rendering and event-handling are a hassle to
test and Hugh's rule is "favor functionality tests over unit tests,
UIs especially". These tests cover:

- The module imports cleanly (catches any stale references from
  the customtkinter version).
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


def test_scheme_combo_populates_from_discovery(qapp, monkeypatch):
    """The scheme combo lists whatever list_available_schemes() returns
    — no hard-coded scheme names in the GUI module."""
    from furrifier import gui as gui_mod
    monkeypatch.setattr(gui_mod, "list_available_schemes",
                        lambda: ["alpha", "beta", "gamma"])
    window = gui_mod.FurrifierWindow()
    try:
        items = [window.scheme_combo.itemText(i)
                 for i in range(window.scheme_combo.count())]
        assert items == ["alpha", "beta", "gamma"]
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




class _FakeLoadOrder:
    """Stand-in for esplib's LoadOrder in picker tests."""

    def __init__(self, plugins):
        self.plugins = list(plugins)


def _picker(monkeypatch, tmp_path, on_disk, load_order, active,
            exclude=None):
    """Build a PluginPickerDialog over a synthetic data dir.

    `on_disk` are files created in tmp_path; `load_order` is what
    plugins.txt claims; `active` is the subset marked active.
    """
    from furrifier import gui as gui_mod
    for name in on_disk:
        (tmp_path / name).write_bytes(b"")

    def fake_from_game(game_id, active_only=False):
        names = active if active_only else load_order
        return _FakeLoadOrder(names)

    monkeypatch.setattr(gui_mod.LoadOrder, "from_game",
                        staticmethod(fake_from_game))
    return gui_mod.PluginPickerDialog(
        None, data_dir=tmp_path, exclude=exclude)


def test_picker_lists_only_plugins_present_on_disk(qapp, monkeypatch,
                                                   tmp_path):
    """The list is plugins.txt INTERSECTED with the data dir, not their
    union. A load-order entry with no file can't be loaded, so offering
    it just produces a run that reports it missing."""
    dialog = _picker(
        monkeypatch, tmp_path,
        on_disk=["Skyrim.esm", "Present.esp", "OnDiskOnly.esp"],
        load_order=["Skyrim.esm", "Ghost.esp", "Present.esp"],
        active=["Skyrim.esm", "Ghost.esp", "Present.esp"])
    try:
        listed = [dialog.list_widget.item(i).data(
                      gui_role()).lower()
                  for i in range(dialog.list_widget.count())]
        assert "ghost.esp" not in listed
        # Load-order order preserved, disk-only extras appended.
        assert listed == ["skyrim.esm", "present.esp", "ondiskonly.esp"]
    finally:
        dialog.deleteLater()


def test_picker_warns_about_active_plugins_not_on_disk(qapp, monkeypatch,
                                                       tmp_path):
    """Active plugins.txt entries that got dropped are reported, not
    silently swallowed — that silence is what made a 1-of-259 run look
    identical to a working one."""
    dialog = _picker(
        monkeypatch, tmp_path,
        on_disk=["Skyrim.esm"],
        load_order=["Skyrim.esm", "Ghost.esp", "Stale.esp"],
        active=["Skyrim.esm", "Ghost.esp"])
    try:
        assert dialog._missing_active == ["Ghost.esp"]
    finally:
        dialog.deleteLater()


def test_picker_does_not_warn_about_inactive_or_excluded(qapp, monkeypatch,
                                                         tmp_path):
    """An inactive stale line is noise, and the patch we're about to
    write doesn't exist yet by definition — neither is a warning."""
    dialog = _picker(
        monkeypatch, tmp_path,
        on_disk=["Skyrim.esm"],
        load_order=["Skyrim.esm", "Stale.esp", "YASNPCPatch.esp"],
        active=["Skyrim.esm", "YASNPCPatch.esp"],
        exclude="yasnpcpatch.esp")
    try:
        assert dialog._missing_active == []
    finally:
        dialog.deleteLater()


def gui_role():
    from PySide6.QtCore import Qt
    return Qt.ItemDataRole.UserRole


# --- --data-dir switch + data-dir change handling ---------------------------


def test_gui_args_parses_data_dir_and_leaves_qt_switches(qapp):
    """--data-dir is ours; everything else must reach QApplication.

    parse_known_args, not parse_args — Qt owns -style/-platform and
    swallowing them would break them."""
    from furrifier.gui import _parse_gui_args
    assert _parse_gui_args(["gui.exe"]) == (None, ["gui.exe"])
    assert _parse_gui_args(["gui.exe", "--data-dir", r"D:\ML\Stock Game\Data"]) \
        == (r"D:\ML\Stock Game\Data", ["gui.exe"])
    assert _parse_gui_args(["gui.exe", "--data-dir", "X", "-style", "fusion"]) \
        == ("X", ["gui.exe", "-style", "fusion"])


def test_data_dir_override_populates_the_field(qapp, tmp_path):
    """--data-dir wins over auto-detection — that's the point of it:
    auto-detection finds the Steam install, which under a Wabbajack
    stock-game modlist is the one Data folder without the mods."""
    from furrifier import gui as gui_mod
    window = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
    try:
        assert window.data_dir_edit.text() == str(tmp_path)
    finally:
        window.deleteLater()


def test_changing_data_dir_clears_the_plugin_selection(qapp, tmp_path):
    """A selection is filenames valid in one directory. Carried into
    another it misreports what a run will actually load."""
    from furrifier import gui as gui_mod
    window = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
    try:
        window._plugin_override = ["Skyrim.esm", "SomeMod.esp"]
        window.plugins_label.setText("2 plugin(s) selected")

        other = tmp_path / "other"
        other.mkdir()
        window.data_dir_edit.setText(str(other))
        window._on_data_dir_changed()

        assert window._plugin_override is None
        assert window.plugins_label.text() == "(using active load order)"
    finally:
        window.deleteLater()


def test_same_data_dir_keeps_the_plugin_selection(qapp, tmp_path):
    """Re-focusing the field, or a Browse landing on the same folder,
    is not a change — clearing there would be a nasty surprise."""
    from furrifier import gui as gui_mod
    window = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
    try:
        window._plugin_override = ["Skyrim.esm"]
        # Same directory, spelled differently: trailing separator.
        window.data_dir_edit.setText(str(tmp_path) + "\\")
        window._on_data_dir_changed()
        assert window._plugin_override == ["Skyrim.esm"]
    finally:
        window.deleteLater()


def test_data_dir_change_warns_when_load_order_is_absent(qapp, tmp_path,
                                                         monkeypatch, caplog):
    """The MO2 case: the active load order is real, the chosen directory
    holds none of it. Say so at the moment of choosing, not after a
    fifteen-second load that yields an empty patch."""
    import logging
    from furrifier import gui as gui_mod

    class _LO:
        plugins = ["Skyrim.esm", "FurryMod.esp", "OtherMod.esp"]

    monkeypatch.setattr(gui_mod.LoadOrder, "from_game",
                        staticmethod(lambda *a, **k: _LO()))
    (tmp_path / "Skyrim.esm").write_bytes(b"")

    window = gui_mod.FurrifierWindow(data_dir=str(tmp_path))
    try:
        with caplog.at_level(logging.WARNING):
            window._warn_if_load_order_absent(str(tmp_path))
        assert "2 of 3 active plugins are not in" in caplog.text
        assert "Mod Organizer" in caplog.text
    finally:
        window.deleteLater()


def test_typo_in_our_switch_is_reported_not_swallowed(qapp):
    """parse_known_args passes unknown switches to Qt without a word.
    Here that silence is dangerous: `--datadir` for `--data-dir` starts
    with the auto-detected Steam folder and looks entirely normal, which
    is the precise failure --data-dir exists to prevent."""
    from furrifier import gui as gui_mod

    gui_mod._startup_notes.clear()
    data_dir, qt_argv = gui_mod._parse_gui_args(["gui.exe", "--datadir", "X"])
    try:
        assert data_dir is None
        assert qt_argv == ["gui.exe", "--datadir", "X"]  # still Qt's to see
        note = " ".join(gui_mod._startup_notes)
        assert "--datadir" in note and "check the spelling" in note
    finally:
        gui_mod._startup_notes.clear()


def test_bad_switch_does_not_kill_a_windowed_build(qapp, monkeypatch):
    """A windowed PyInstaller build has no stdout/stderr. argparse writes
    --help and its errors there, so `--data-dir` with no value used to
    raise inside argparse and kill the GUI before it drew anything - a
    typo in an MO2 executable definition with no visible cause. Start
    anyway, and keep the message for the log pane."""
    import sys as _sys
    from furrifier import gui as gui_mod

    gui_mod._startup_notes.clear()
    monkeypatch.setattr(_sys, "stdout", None)
    monkeypatch.setattr(_sys, "stderr", None)

    data_dir, qt_argv = gui_mod._parse_gui_args(["gui.exe", "--data-dir"])
    try:
        assert data_dir is None
        assert qt_argv == ["gui.exe"]
        assert gui_mod._startup_notes, "the reason must survive for the pane"
        assert "Command line ignored" in gui_mod._startup_notes[0]
    finally:
        gui_mod._startup_notes.clear()
