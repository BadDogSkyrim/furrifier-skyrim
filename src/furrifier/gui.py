"""Furrifier GUI (PySide6).

Ported from the customtkinter version in 2026-04-22 to open the door
to an embedded 3D preview pane (see PLAN_FURRIFIER_PREVIEW.md). The
widget layout, field wiring, and worker-thread pattern match the prior
version one-for-one; only the toolkit changed. Phase 1 deliberately
has no new features — the preview pane arrives in Phase 3.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QObject, QThread, Signal
from PySide6.QtGui import QAction, QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from esplib import LoadOrder, find_game_data
from esplib.record import Record
from esplib.utils import BinaryReader

from .config import FurrifierConfig
from .main import run_furrification
from .race_defs import list_available_schemes


PLUGIN_EXTS = {".esp", ".esm", ".esl"}


def _read_plugin_masters(path: Path) -> list[str]:
    """Return the masters declared in a plugin's TES4 header.

    Reads and parses only the header record, not the full plugin body.
    Returns [] on any read/parse error — callers treat masters as a
    best-effort hint, not a hard requirement.
    """
    try:
        # TES4 is the first record; 64KB is enough even for plugins with
        # long master lists and override-record blocks.
        with open(path, "rb") as f:
            data = f.read(65536)
        reader = BinaryReader(data)
        header = Record.from_bytes(reader)
        if header.signature != "TES4":
            return []
        return [sub.get_string() for sub in header.subrecords
                if sub.signature == "MAST"]
    except Exception:
        return []


def _asset_path(name: str) -> Path:
    """Locate an asset file in dev mode or inside a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "furrifier" / "assets" / name  # type: ignore[attr-defined]
    return Path(__file__).parent / "assets" / name


# --- logging bridge ---------------------------------------------------------


class _LogBridge(QObject):
    """QObject whose sole job is to own a Qt signal for log lines.

    logging.Handler isn't a QObject, so it can't have signals of its
    own. We route emits through this bridge — the handler .emit()s
    into bridge.new_log, which the GUI thread picks up via a normal
    signal/slot connection (queued across the thread boundary).
    """
    new_log = Signal(str)


class _QtLogHandler(logging.Handler):
    def __init__(self, bridge: _LogBridge):
        super().__init__()
        self._bridge = bridge
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.new_log.emit(self.format(record))
        except Exception:
            self.handleError(record)


# --- worker thread ----------------------------------------------------------


class _Worker(QThread):
    """Runs run_furrification on a background thread. Progress and
    completion flow back to the GUI via signals."""

    phase = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, config: FurrifierConfig,
                 load_order: Optional[LoadOrder],
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._load_order = load_order

    def run(self) -> None:  # noqa: D401 — QThread.run override
        try:
            run_furrification(
                self._config, load_order=self._load_order,
                progress=lambda p: self.phase.emit(p))
            self.finished_ok.emit()
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Furrification failed: %s", exc)
            self.failed.emit(str(exc))


# --- main window ------------------------------------------------------------


class FurrifierWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Skyrim Furrifier")
        self.resize(820, 820)
        self.setMinimumSize(720, 620)

        self._worker: Optional[_Worker] = None
        self._file_handler: Optional[logging.FileHandler] = None
        # None = use active load order from plugins.txt; a list = explicit
        # selection from the plugin picker.
        self._plugin_override: Optional[list[str]] = None

        self._build_widgets()
        self._apply_icon()
        # Persistent bridge: log output from BOTH the Run and Preview
        # paths flows into the log pane. Run's _install_log_handler
        # layers additional bits on top (file handler, debug level).
        self._install_persistent_log_bridge()

    # --- layout ------------------------------------------------------------

    def _build_widgets(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Split pane: config + log on the left, live preview on the right.
        splitter = QSplitter(Qt.Orientation.Horizontal, central)
        outer.addWidget(splitter)

        # --- left: banner + form + options + log + bottom bar ---
        left = QWidget(splitter)
        root = QVBoxLayout(left)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(6)
        banner_path = _asset_path("banner.png")
        if banner_path.is_file():
            banner = QLabel(left)
            banner.setPixmap(QPixmap(str(banner_path)))
            banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
            root.addWidget(banner)
        root.addWidget(self._build_form(left))
        root.addWidget(self._build_options(left))
        root.addWidget(self._build_log_pane(left), stretch=1)
        root.addWidget(self._build_bottom_bar(left))
        splitter.addWidget(left)

        # --- right: live NPC preview pane ---
        # Lazy import — pulls in PySide6.QtQuickWidgets + QtQuick3D
        # which aren't free, and we don't need them unless the window
        # actually opens.
        from .preview import PreviewPane
        self.preview_pane = PreviewPane(
            config_provider=self._config_from_fields,
            load_order_provider=self._build_preview_load_order,
            parent=splitter)
        splitter.addWidget(self.preview_pane)

        # Scheme change invalidates the session (different race
        # assignments → different furry output). Tell the preview
        # pane to rebuild + re-bake the currently-visible NPC.
        self.scheme_combo.currentIndexChanged.connect(
            lambda _i: self.preview_pane.refresh_on_scheme_change())

        # Mirror the Run path's log setup on Preview's Load button —
        # without this, Preview output never hits the user's log file
        # and debug toggles don't apply.
        self.preview_pane.load_button.clicked.connect(
            lambda: self._install_log_handler(self._config_from_fields()))

        # Left pane opens at the banner's natural width (plus the
        # pane's own 12px content margin on each side). The preview
        # gets everything that's left; user can drag the splitter
        # handle after open. `heightForWidth` on the scene widget
        # inside QSplitter doesn't work reliably, so we size the
        # window so the default layout gives the preview enough
        # room to show its 3:4 portrait viewport unclipped.
        banner_pad = 12 * 2  # left-pane QVBoxLayout's margins
        banner_w = 0
        if banner_path.is_file():
            banner_w = QPixmap(str(banner_path)).width()
        left_w = max(banner_w + banner_pad, 320)
        # Window width = left pane + preview pane. For the preview's
        # 3:4 portrait at default height, its width needs to be
        # height * 3/4. Pick a window height that gives the preview
        # enough vertical room to fit 3:4 without pillarboxing.
        window_h = 960
        preview_w = (window_h * 3) // 4
        self.resize(left_w + preview_w, window_h)
        splitter.setSizes([left_w, preview_w])

    def _build_form(self, parent: QWidget) -> QWidget:
        frame = QFrame(parent)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        grid = QGridLayout(frame)
        grid.setColumnStretch(1, 1)

        row = 0

        # Scheme
        grid.addWidget(QLabel("Scheme:"), row, 0)
        self.scheme_combo = QComboBox(frame)
        self.scheme_combo.addItems(list_available_schemes())
        self.scheme_combo.setCurrentText("all_races")
        grid.addWidget(self.scheme_combo, row, 1, 1, 2)
        row += 1

        # Patch filename
        grid.addWidget(QLabel("Patch file:"), row, 0)
        self.patch_edit = QLineEdit("YASNPCPatch.esp", frame)
        grid.addWidget(self.patch_edit, row, 1, 1, 2)
        row += 1

        # Data dir
        grid.addWidget(QLabel("Data dir:"), row, 0)
        detected = find_game_data('tes5')
        self.data_dir_edit = QLineEdit(str(detected) if detected else "", frame)
        self.data_dir_edit.setPlaceholderText("(not auto-detected)")
        grid.addWidget(self.data_dir_edit, row, 1)
        browse_data = QPushButton("Browse...", frame)
        browse_data.clicked.connect(self._browse_data_dir)
        grid.addWidget(browse_data, row, 2)
        row += 1

        # Output dir
        grid.addWidget(QLabel("Output dir:"), row, 0)
        self.output_dir_edit = QLineEdit("", frame)
        self.output_dir_edit.setPlaceholderText("(same as Data dir)")
        grid.addWidget(self.output_dir_edit, row, 1)
        browse_out = QPushButton("Browse...", frame)
        browse_out.clicked.connect(self._browse_output_dir)
        grid.addWidget(browse_out, row, 2)
        row += 1

        # Plugins
        grid.addWidget(QLabel("Plugins:"), row, 0)
        self.plugins_label = QLabel("(using active load order)", frame)
        grid.addWidget(self.plugins_label, row, 1)
        edit_plugins = QPushButton("Edit plugins...", frame)
        edit_plugins.clicked.connect(self._open_plugin_picker)
        grid.addWidget(edit_plugins, row, 2)
        row += 1

        # Log file
        grid.addWidget(QLabel("Log file:"), row, 0)
        self.log_file_edit = QLineEdit("", frame)
        self.log_file_edit.setPlaceholderText("(optional)")
        grid.addWidget(self.log_file_edit, row, 1)
        browse_log = QPushButton("Browse...", frame)
        browse_log.clicked.connect(self._browse_log_file)
        grid.addWidget(browse_log, row, 2)

        return frame

    def _build_options(self, parent: QWidget) -> QWidget:
        frame = QFrame(parent)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(frame)
        self.armor_cb = QCheckBox("Furrify armor", frame)
        self.schlongs_cb = QCheckBox("Schlongs (SOS)", frame)
        self.facegen_cb = QCheckBox("Build FaceGen", frame)
        self.debug_cb = QCheckBox("Debug logging", frame)
        for cb in (self.armor_cb, self.schlongs_cb, self.facegen_cb):
            cb.setChecked(True)
            layout.addWidget(cb)
        layout.addWidget(self.debug_cb)
        layout.addStretch(1)
        # Face-tint output size. "Auto" preserves the compositor's
        # native-mask-size default; explicit powers of 2 force a
        # Lanczos resample to that edge length.
        layout.addWidget(QLabel("Tint size:", frame))
        self.facetint_size_combo = QComboBox(frame)
        self.facetint_size_combo.addItem("Auto", None)
        for size in (256, 512, 1024, 2048, 4096):
            self.facetint_size_combo.addItem(str(size), size)
        layout.addWidget(self.facetint_size_combo)
        # FaceGen NPC cap. Integer > 0, blank = no cap. Preview a
        # scheme on a handful of NPCs without paying for a full bake.
        layout.addWidget(QLabel("FaceGen limit:", frame))
        self.facegen_limit_edit = QLineEdit(frame)
        self.facegen_limit_edit.setPlaceholderText("(all)")
        self.facegen_limit_edit.setValidator(QIntValidator(1, 1_000_000, frame))
        self.facegen_limit_edit.setFixedWidth(80)
        layout.addWidget(self.facegen_limit_edit)
        return frame

    def _build_log_pane(self, parent: QWidget) -> QWidget:
        self.log_text = QPlainTextEdit(parent)
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(5000)
        # Monospace so log output lines up.
        font = self.log_text.font()
        font.setFamily("Consolas")
        self.log_text.setFont(font)
        return self.log_text

    def _build_bottom_bar(self, parent: QWidget) -> QWidget:
        frame = QFrame(parent)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QHBoxLayout(frame)
        self.phase_label = QLabel("Ready.", frame)
        layout.addWidget(self.phase_label, stretch=1)
        self.run_button = QPushButton("Run", frame)
        self.run_button.clicked.connect(self._start_run)
        self.run_button.setFixedWidth(120)
        # Primary-action styling: filled accent per QSS property
        # selector. Only one primary button in the window.
        self.run_button.setProperty("primary", True)
        layout.addWidget(self.run_button)
        return frame

    def _apply_icon(self) -> None:
        ico_path = _asset_path("furrifier.ico")
        if ico_path.is_file():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(ico_path)))

    def closeEvent(self, event) -> None:
        # Preview pane owns a QThread — give it a chance to exit.
        if hasattr(self, "preview_pane"):
            self.preview_pane.shutdown()
        super().closeEvent(event)

    # --- actions -----------------------------------------------------------

    def _browse_data_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select Skyrim Data directory",
            self.data_dir_edit.text() or "")
        if path:
            self.data_dir_edit.setText(path)

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select output directory",
            self.output_dir_edit.text() or self.data_dir_edit.text() or "")
        if path:
            self.output_dir_edit.setText(path)

    def _browse_log_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Log file", "", "Log files (*.log);;All files (*)")
        if path:
            self.log_file_edit.setText(path)

    def _open_plugin_picker(self) -> None:
        data_dir_str = self.data_dir_edit.text().strip()
        if not data_dir_str:
            QMessageBox.critical(
                self, "Plugins",
                "Set a data directory before picking plugins.")
            return
        data_dir = Path(data_dir_str)
        if not data_dir.is_dir():
            QMessageBox.critical(
                self, "Plugins", f"Data directory not found: {data_dir}")
            return
        # Exclude the patch itself from the picker — no reason to
        # include our own output as an input, and doing so leaves
        # stale data from previous runs in the load order.
        patch_name = (self.patch_edit.text().strip().lower()
                      or "yasnpcpatch.esp")
        dialog = PluginPickerDialog(
            self, data_dir=data_dir,
            initial_selection=self._plugin_override,
            exclude=patch_name)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._plugin_override = dialog.result
            self.plugins_label.setText(
                f"{len(self._plugin_override)} plugin(s) selected")
            # Propagate to the preview pane so it re-loads plugins
            # under the new selection.
            if hasattr(self, "preview_pane"):
                self.preview_pane.refresh_on_plugins_change()

    def _build_preview_load_order(self) -> Optional[LoadOrder]:
        """load_order_provider for the preview pane. Returns the
        user's picked plugins or None to fall back to active_only."""
        config = self._config_from_fields()
        return self._build_load_order(config)

    # --- run ---------------------------------------------------------------

    def _config_from_fields(self) -> FurrifierConfig:
        patch = self.patch_edit.text().strip() or "YASNPCPatch.esp"
        if Path(patch).suffix.lower() not in PLUGIN_EXTS:
            patch += ".esp"
        limit_text = self.facegen_limit_edit.text().strip()
        facegen_limit = int(limit_text) if limit_text else None
        return FurrifierConfig(
            patch_filename=patch,
            race_scheme=self.scheme_combo.currentText(),
            furrify_armor=self.armor_cb.isChecked(),
            furrify_schlongs=self.schlongs_cb.isChecked(),
            build_facegen=self.facegen_cb.isChecked(),
            debug=self.debug_cb.isChecked(),
            log_file=self.log_file_edit.text().strip() or None,
            game_data_dir=self.data_dir_edit.text().strip() or None,
            output_dir=self.output_dir_edit.text().strip() or None,
            facegen_limit=facegen_limit,
            facetint_size=self.facetint_size_combo.currentData(),
        )

    def _build_load_order(
            self, config: FurrifierConfig) -> Optional[LoadOrder]:
        if self._plugin_override is None:
            return None
        data_dir = (Path(config.game_data_dir) if config.game_data_dir
                    else None)
        return LoadOrder.from_list(
            self._plugin_override, data_dir=data_dir, game_id="tes5")

    def _start_run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        config = self._config_from_fields()
        load_order = self._build_load_order(config)

        self.log_text.clear()
        self._install_log_handler(config)
        self.run_button.setEnabled(False)
        self.run_button.setText("Running...")
        self.phase_label.setText("Starting...")

        worker = _Worker(config, load_order, parent=self)
        worker.phase.connect(self.phase_label.setText)
        worker.finished_ok.connect(self._on_finished_ok)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_finished_ok(self) -> None:
        self._remove_log_handler()
        self.run_button.setEnabled(True)
        self.run_button.setText("Run")
        self.phase_label.setText("Done.")
        self._worker = None

    def _on_failed(self, message: str) -> None:
        self._remove_log_handler()
        self.run_button.setEnabled(True)
        self.run_button.setText("Run")
        self.phase_label.setText("Failed.")
        QMessageBox.critical(self, "Furrifier",
                             f"Furrification failed:\n{message}")
        self._worker = None

    # --- log plumbing ------------------------------------------------------

    def _install_persistent_log_bridge(self) -> None:
        """Attach a root-logger handler that mirrors everything
        (INFO and above) into the log pane. Stays for the window's
        whole life so Preview's session-setup messages, not just
        Run's, show up."""
        root = logging.getLogger()
        if root.level > logging.INFO or root.level == logging.NOTSET:
            root.setLevel(logging.INFO)
        bridge = _LogBridge(self)
        bridge.new_log.connect(
            self.log_text.appendPlainText,
            Qt.ConnectionType.QueuedConnection)
        handler = _QtLogHandler(bridge)
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
        self._persistent_bridge = bridge
        self._persistent_handler = handler

    def _install_log_handler(self, config: FurrifierConfig) -> None:
        """Apply the log file + debug level from the current config.

        Shared by Run start and Preview's Load-NPCs click — Hugh wants
        one log file field to capture output from both paths. File
        handler attachment is idempotent (first call wins); the mode=
        "w" truncate happens once per window lifetime. Level gets set
        on every call so toggling the debug checkbox takes effect on
        the next Load/Run click.
        """
        level = logging.DEBUG if config.debug else logging.INFO
        root = logging.getLogger()
        self._saved_root_level = root.level
        root.setLevel(level)
        self._persistent_handler.setLevel(level)

        # Attach file handler if not already. Keeps the same handler
        # across Preview picks and Run clicks so the log file captures
        # the whole session.
        if self._file_handler is None and config.log_file:
            try:
                log_path = Path(config.log_file).resolve()
                log_path.parent.mkdir(parents=True, exist_ok=True)
                fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
                fh.setLevel(level)
                fh.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"))
                root.addHandler(fh)
                self._file_handler = fh
            except OSError as exc:
                logging.getLogger(__name__).warning(
                    "could not open log file %r: %s", config.log_file, exc)
        elif self._file_handler is not None:
            # Already attached — make sure its level matches the
            # current debug setting.
            self._file_handler.setLevel(level)

    def _remove_log_handler(self) -> None:
        # File handler stays attached for the window's life; Run end
        # just restores the log level so Preview doesn't inherit the
        # Run-side debug bump if debug was off for Preview.
        if hasattr(self, "_saved_root_level"):
            root = logging.getLogger()
            root.setLevel(self._saved_root_level or logging.INFO)
            self._persistent_handler.setLevel(root.level)


# --- plugin picker ----------------------------------------------------------


class PluginPickerDialog(QDialog):
    """Modal checkbox list for picking which plugins to run against.

    Lists every *.esp/*.esm/*.esl in the data dir in load-order order.
    Plugins currently marked active in plugins.txt are pre-checked;
    others are unchecked. Checking a plugin automatically pulls in its
    transitive masters (parsed from each plugin's TES4 header).
    """

    def __init__(self, parent: QWidget, data_dir: Path,
                 initial_selection: Optional[list[str]] = None,
                 exclude: Optional[str] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select plugins")
        self.resize(520, 640)
        self.setModal(True)

        self.result: Optional[list[str]] = None
        self._data_dir = data_dir
        # Cache of plugin-name.lower() -> list of master names.
        self._master_cache: dict[str, list[str]] = {}
        # Plugin names (lowercased) to hide from the list entirely —
        # typically the patch output file, which shouldn't be
        # re-ingested as input.
        self._exclude: set[str] = {exclude.lower()} if exclude else set()

        plugins_in_order = self._collect_plugins(data_dir)
        if initial_selection is not None:
            checked = {p.lower() for p in initial_selection}
        else:
            checked = self._active_plugins()

        self._build_widgets(plugins_in_order, checked)

    def _collect_plugins(self, data_dir: Path) -> list[str]:
        """Full ordered plugin list: load-order first, then any extras on disk."""
        load_order_names: list[str] = []
        try:
            lo = LoadOrder.from_game("tes5", active_only=False)
            load_order_names = list(lo.plugins)
        except Exception:
            pass

        on_disk: list[str] = []
        if data_dir.is_dir():
            for entry in sorted(data_dir.iterdir(), key=lambda p: p.name.lower()):
                if entry.is_file() and entry.suffix.lower() in PLUGIN_EXTS:
                    on_disk.append(entry.name)

        seen_lower = {name.lower() for name in load_order_names}
        extras = [name for name in on_disk if name.lower() not in seen_lower]
        combined = load_order_names + extras
        if self._exclude:
            combined = [n for n in combined if n.lower() not in self._exclude]
        return combined

    def _active_plugins(self) -> set[str]:
        try:
            lo = LoadOrder.from_game("tes5", active_only=True)
            return {name.lower() for name in lo.plugins}
        except Exception:
            return set()

    def _build_widgets(self, plugins: list[str],
                       checked: set[str]) -> None:
        layout = QVBoxLayout(self)

        # Filter row
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit(self)
        self.filter_edit.setPlaceholderText(
            "substring match, case-insensitive")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit, stretch=1)
        layout.addLayout(filter_row)

        self.summary_label = QLabel("", self)
        layout.addWidget(self.summary_label)

        # The list itself. Each item stores its plugin name in UserRole.
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(
            self._show_context_menu)
        # Check-state changes are the signal we key auto-master-add off of.
        self.list_widget.itemChanged.connect(self._on_item_changed)

        # Track which item changes are user-originated vs. internally
        # driven (e.g. when pulling in masters). Without this the check
        # of a master would recurse into pulling in ITS masters mid-
        # iteration, which is fine, but we suppress the cascade briefly
        # so master-of-master toggles don't each emit a UI-update.
        self._user_toggle_in_progress = False

        for name in plugins:
            item = QListWidgetItem(name, self.list_widget)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            # setData MUST come before setCheckState — the check-state
            # change fires itemChanged synchronously, which dispatches
            # _on_item_changed → _pull_in_masters → item.data(UserRole).
            # If data isn't set yet, that returns None and crashes.
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setCheckState(
                Qt.CheckState.Checked if name.lower() in checked
                else Qt.CheckState.Unchecked)

        layout.addWidget(self.list_widget, stretch=1)

        # Bottom buttons
        button_row = QHBoxLayout()
        reset_btn = QPushButton("Reset", self)
        reset_btn.clicked.connect(self._reset)
        button_row.addWidget(reset_btn)
        button_row.addStretch(1)
        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        ok_btn = QPushButton("OK", self)
        ok_btn.clicked.connect(self._on_ok)
        ok_btn.setDefault(True)
        button_row.addWidget(ok_btn)
        layout.addLayout(button_row)

        self._update_summary()

    # --- list helpers ------------------------------------------------------

    def _all_items(self) -> list[QListWidgetItem]:
        return [self.list_widget.item(i)
                for i in range(self.list_widget.count())]

    def _visible_items(self) -> list[QListWidgetItem]:
        return [it for it in self._all_items() if not it.isHidden()]

    def _by_name_lower(self, name: str) -> Optional[QListWidgetItem]:
        target = name.lower()
        for it in self._all_items():
            if it.data(Qt.ItemDataRole.UserRole).lower() == target:
                return it
        return None

    def _apply_filter(self) -> None:
        query = self.filter_edit.text().strip().lower()
        for it in self._all_items():
            name = it.data(Qt.ItemDataRole.UserRole).lower()
            it.setHidden(bool(query) and query not in name)
        self._update_summary()

    def _update_summary(self) -> None:
        total = self.list_widget.count()
        checked = sum(1 for it in self._all_items()
                      if it.checkState() == Qt.CheckState.Checked)
        visible = len(self._visible_items())
        if visible == total:
            self.summary_label.setText(f"{checked} / {total} checked")
        else:
            self.summary_label.setText(
                f"{checked} / {total} checked ({visible} shown)")

    # --- master pull-in ----------------------------------------------------

    def _get_masters(self, name: str) -> list[str]:
        key = name.lower()
        cached = self._master_cache.get(key)
        if cached is not None:
            return cached
        path = self._data_dir / name
        masters = _read_plugin_masters(path) if path.is_file() else []
        self._master_cache[key] = masters
        return masters

    def _pull_in_masters(self, name: str) -> None:
        """Check every transitive master of `name` that we know about."""
        seen: set[str] = set()
        queue = [name]
        while queue:
            current = queue.pop()
            for master in self._get_masters(current):
                key = master.lower()
                if key in seen:
                    continue
                seen.add(key)
                item = self._by_name_lower(master)
                if item is not None and item.checkState() != Qt.CheckState.Checked:
                    # Set check state directly without re-triggering the
                    # master-pull loop (we're already handling it here).
                    self._user_toggle_in_progress = True
                    try:
                        item.setCheckState(Qt.CheckState.Checked)
                    finally:
                        self._user_toggle_in_progress = False
                queue.append(master)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Fires for every check-state change. Pull in masters only for
        genuine user-toggles (not our own master-cascade mutations)."""
        if self._user_toggle_in_progress:
            self._update_summary()
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._pull_in_masters(item.data(Qt.ItemDataRole.UserRole))
        self._update_summary()

    # --- context menu ------------------------------------------------------

    def _show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        # Keyboard mnemonics follow xEdit conventions: C for Check all,
        # E for uncheck (since Uncheck overlaps with Check on C), I for
        # Invert. Qt QAction uses `&` to mark the accelerator.
        check_all = QAction("&Check all", menu)
        check_all.triggered.connect(self._check_all)
        uncheck_all = QAction("Unch&eck all", menu)
        uncheck_all.triggered.connect(self._uncheck_all)
        invert = QAction("&Invert selection", menu)
        invert.triggered.connect(self._invert)
        menu.addAction(check_all)
        menu.addAction(uncheck_all)
        menu.addAction(invert)
        menu.exec(self.list_widget.mapToGlobal(pos))

    def _check_all(self) -> None:
        for it in self._visible_items():
            it.setCheckState(Qt.CheckState.Checked)

    def _uncheck_all(self) -> None:
        for it in self._visible_items():
            it.setCheckState(Qt.CheckState.Unchecked)

    def _invert(self) -> None:
        for it in self._visible_items():
            it.setCheckState(
                Qt.CheckState.Unchecked
                if it.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked)

    def _reset(self) -> None:
        """Restore to currently-active plugins per plugins.txt."""
        active = self._active_plugins()
        for it in self._all_items():
            name = it.data(Qt.ItemDataRole.UserRole)
            it.setCheckState(
                Qt.CheckState.Checked if name.lower() in active
                else Qt.CheckState.Unchecked)

    # --- close -------------------------------------------------------------

    def _on_ok(self) -> None:
        self.result = [
            it.data(Qt.ItemDataRole.UserRole)
            for it in self._all_items()
            if it.checkState() == Qt.CheckState.Checked
        ]
        self.accept()


# --- entry point ------------------------------------------------------------


def main() -> int:
    # Windows groups all Python GUI apps under the interpreter's
    # taskbar icon unless the process declares its own
    # AppUserModelID. The packaged exe gets its icon from the .exe
    # metadata directly (see PyInstaller spec), so this shim is
    # just for dev mode.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "BadDogSkyrim.Furrifier.1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    # QSS needs an absolute URL for the check-tick image. Simple
    # substitution (not str.format — QSS has lots of unrelated
    # curly braces that would trip .format()).
    check_url = _asset_path("check.svg").resolve().as_posix()
    app.setStyleSheet(
        _APP_STYLESHEET.replace("{check_icon}", check_url))
    window = FurrifierWindow()
    window.show()
    return app.exec()


# Warm-dark "fantasy mod tool" palette. Tokens live at the top of
# the sheet for easy tweaking.
#
#   bg              #1C1917   main window
#   surface         #26231F   cards, inputs, log pane
#   border/input    #3A342D
#   ghost border    #4D463C
#   accent          #CBA568   Run, active borders, checkbox tick
#   accent text     #EFD49A   on dark fills when hovered
#   check-bg        #4D3C20   filled checkbox background
#   primary text    #E0D9CC
#   ghost text      #BFB5A3
#   label text      #968E83
#   placeholder     #6E665A
#
# Conventions:
#   - Primary action uses `primary="true"` property → filled accent.
#   - Every other QPushButton is a ghost (outlined, transparent fill).
#   - Disabled state drops color intensity everywhere. Primary loses
#     its fill too, so it reads like a ghost that's off.
_APP_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1C1917;
    color: #E0D9CC;
}

QLabel {
    color: #968E83;
    background-color: transparent;
}

QLineEdit, QComboBox, QPlainTextEdit {
    background-color: #26231F;
    color: #E0D9CC;
    border: 1px solid #3A342D;
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #4D3C20;
    selection-color: #EFD49A;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {
    border: 1px solid #CBA568;
}
QLineEdit::placeholder {
    color: #6E665A;
}

QComboBox::drop-down {
    border: none;
}
QComboBox QAbstractItemView {
    background-color: #26231F;
    color: #E0D9CC;
    border: 1px solid #3A342D;
    selection-background-color: #4D3C20;
    selection-color: #EFD49A;
}

QFrame {
    background-color: transparent;
    border: none;
}

/* Ghost buttons — transparent fill, gold outline on hover. Default
   for everything except the primary action. */
QPushButton {
    background-color: transparent;
    color: #BFB5A3;
    border: 1px solid #4D463C;
    border-radius: 4px;
    padding: 4px 14px;
    min-height: 14px;
}
QPushButton:hover {
    /* ~50% mix of accent with bg — gold-ish but calmer than primary. */
    background-color: #635039;
    color: #EFD49A;
    border-color: #CBA568;
}
QPushButton:pressed {
    background-color: #4D3C20;
}
QPushButton:disabled {
    color: #6E665A;
    border-color: #3A342D;
}

/* Primary button — filled accent. Tag a button with
   setProperty("primary", True) to pick this up. */
QPushButton[primary="true"] {
    background-color: #CBA568;
    color: #1C1917;
    border: 1px solid #CBA568;
}
QPushButton[primary="true"]:hover {
    background-color: #D7B47A;
    border-color: #D7B47A;
}
QPushButton[primary="true"]:pressed {
    background-color: #B08E52;
}
QPushButton[primary="true"]:disabled {
    background-color: transparent;   /* lose the fill — "off" state */
    color: #6E665A;
    border-color: #3A342D;
}

QCheckBox {
    color: #E0D9CC;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 14px; height: 14px;
    border-radius: 3px;
}
QCheckBox::indicator:unchecked {
    background-color: transparent;
    border: 1px solid #4D463C;
}
QCheckBox::indicator:checked {
    background-color: #4D3C20;
    border: 1px solid #CBA568;
    /* Qt's default tick glyph disappears once the indicator has a
       styled background; load our own gold-tick SVG. The path is
       substituted at app start via str.format to handle both dev
       and PyInstaller asset layouts. */
    image: url("{check_icon}");
}
QCheckBox::indicator:disabled {
    border-color: #3A342D;
}

/* List / tree items in the plugin picker. */
QListWidget, QTreeWidget {
    background-color: #26231F;
    color: #E0D9CC;
    border: 1px solid #3A342D;
    selection-background-color: #4D3C20;
    selection-color: #EFD49A;
}
"""


if __name__ == "__main__":
    sys.exit(main())
