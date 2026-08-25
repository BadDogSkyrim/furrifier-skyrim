"""Furrifier GUI (PySide6).

Ported from the customtkinter version in 2026-04-22 to open the door
to an embedded 3D preview pane (see PLAN_FURRIFIER_PREVIEW.md). The
widget layout, field wiring, and worker-thread pattern match the prior
version one-for-one; only the toolkit changed. Phase 1 deliberately
has no new features — the preview pane arrives in Phase 3.
"""
from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import threading
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
from esplib.utils import is_readable_file, is_listable_dir, ensure_dir

from .build_info import version_string
from .config import FurrifierConfig
from .main import run_furrification
from .race_defs import list_available_schemes
from .session import read_plugin_masters
from .session_cache import SessionCache


PLUGIN_EXTS = {".esp", ".esm", ".esl"}


# Warnings raised before the log pane exists (command-line parsing).
# Drained into the log by FurrifierWindow once the bridge is up —
# otherwise a windowed build has nowhere to put them.
_startup_notes: list[str] = []


def _same_dir(a: str, b: str) -> bool:
    """Compare two directory strings the way Windows would.

    Case-insensitive and indifferent to trailing separators, so
    re-focusing the field or a Browse that lands on the same folder
    doesn't count as a change and blow away the user's selection.
    """
    def norm(s: str) -> str:
        s = (s or "").strip().rstrip("\\/")
        try:
            return str(Path(s)).lower() if s else ""
        except Exception:
            return s.lower()
    return norm(a) == norm(b)


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
    cancelled = Signal()

    def __init__(self, config: FurrifierConfig,
                 load_order: Optional[LoadOrder],
                 cache: SessionCache,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._load_order = load_order
        self._cache = cache
        # Cooperative-cancel flag the pipeline checks at phase
        # boundaries and per-NPC checkpoints. set() is thread-safe.
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:  # noqa: D401 — QThread.run override
        from .main import CancelledError
        try:
            run_furrification(
                self._config, load_order=self._load_order,
                progress=lambda p: self.phase.emit(p),
                cache=self._cache,
                cancel_event=self._cancel_event)
            self.finished_ok.emit()
        except CancelledError:
            logging.getLogger(__name__).info("Furrification cancelled by user")
            self.cancelled.emit()
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Furrification failed: %s", exc)
            self.failed.emit(str(exc))


# --- main window ------------------------------------------------------------


class FurrifierWindow(QMainWindow):
    def __init__(self, data_dir: Optional[str] = None) -> None:
        super().__init__()
        self.setWindowTitle(f"Skyrim Furrifier {version_string()}")
        self.resize(820, 820)
        self.setMinimumSize(720, 620)

        # Overrides the auto-detected Data dir. Set from --data-dir so a
        # Mod Organizer executable definition can launch us pointed at
        # the Data folder MO2 actually virtualizes — auto-detection
        # finds the Steam install, which for a Wabbajack stock-game
        # modlist is the one folder guaranteed NOT to hold the mods.
        self._data_dir_override = data_dir

        self._worker: Optional[_Worker] = None
        self._file_handler: Optional[logging.FileHandler] = None
        # None = use active load order from plugins.txt; a list = explicit
        # selection from the plugin picker.
        self._plugin_override: Optional[list[str]] = None
        # Shared cache: preview worker populates it on first preview,
        # Run worker reuses it instead of paying the ~15s plugin load
        # twice. Invalidated after a Run completes (the Run mutates
        # the shared plugin_set with its patch).
        self._session_cache = SessionCache()

        self._build_widgets()
        self._apply_icon()
        # Persistent bridge: log output from BOTH the Run and Preview
        # paths flows into the log pane. Run's _install_log_handler
        # layers additional bits on top (file handler, debug level).
        self._install_persistent_log_bridge()
        # Anything the command-line parse wanted to say had nowhere to
        # go until now.
        while _startup_notes:
            logging.getLogger(__name__).warning("%s", _startup_notes.pop(0))

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
            cache=self._session_cache,
            load_order_provider=self._build_preview_load_order,
            parent=splitter,
            # Mirror the Run path's log setup on Preview's Load click —
            # without this, Preview output never hits the user's log
            # file and debug toggles don't apply. Passed as a hook so
            # it runs *before* the worker dispatches; see the note on
            # PreviewPane._on_load_requested.
            on_load_requested=lambda: self._install_log_handler(
                self._config_from_fields()))
        splitter.addWidget(self.preview_pane)

        # Scheme change invalidates the session (different race
        # assignments → different furry output). Tell the preview
        # pane to rebuild + re-bake the currently-visible NPC.
        self.scheme_combo.currentIndexChanged.connect(
            lambda _i: self.preview_pane.refresh_on_scheme_change())

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
        if self._data_dir_override:
            initial_data_dir = self._data_dir_override
        else:
            detected = find_game_data('tes5')
            initial_data_dir = str(detected) if detected else ""
        self.data_dir_edit = QLineEdit(initial_data_dir, frame)
        self.data_dir_edit.setPlaceholderText("(not auto-detected)")
        # Editing the data dir invalidates any plugin selection made
        # against the old one — see _on_data_dir_changed. editingFinished
        # (Enter / focus-out) rather than textChanged so we don't churn
        # on every keystroke of a typed path; the Browse handler calls it
        # directly because setText doesn't emit editingFinished.
        self._last_data_dir = initial_data_dir
        self.data_dir_edit.editingFinished.connect(self._on_data_dir_changed)
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
        self.facegen_cb = QCheckBox("Build FaceGen", frame)
        self.skip_furry_cb = QCheckBox("Skip furry NPCs", frame)
        self.skip_furry_cb.setToolTip(
            "Skip NPCs whose winning override is already a furrifier "
            "output. Use to extend a curated patch with new mods' NPCs "
            "without re-deriving the existing ones.")
        self.debug_cb = QCheckBox("Debug logging", frame)
        self.throttle_cb = QCheckBox("Throttle FaceGen", frame)
        self.throttle_cb.setToolTip(
            "Cap FaceGen at one BELOW_NORMAL-priority worker so the "
            "machine stays responsive. Wall-time roughly matches the "
            "old serial path; useful for long bakes you want to leave "
            "running.")
        for cb in (self.armor_cb, self.facegen_cb):
            cb.setChecked(True)
        layout.addWidget(self.armor_cb)
        layout.addWidget(self.skip_furry_cb)
        layout.addWidget(self.facegen_cb)
        layout.addWidget(self.throttle_cb)
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
        # Build identity, always on screen. A bug report that quotes this
        # tells us exactly which kit produced the output.
        self.version_label = QLabel(version_string(), frame)
        self.version_label.setStyleSheet("color: palette(mid);")
        self.version_label.setToolTip(
            "Furrifier version, build timestamp and source commit.\n"
            "A trailing '+' means the build was cut from a tree with "
            "uncommitted changes.")
        layout.addWidget(self.version_label)
        # Single button doubles as Run / Cancel — its label tracks
        # worker state. _run_or_cancel_clicked dispatches.
        self.run_button = QPushButton("Run", frame)
        self.run_button.clicked.connect(self._run_or_cancel_clicked)
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
            # setText emits textChanged, not editingFinished.
            self._on_data_dir_changed()

    def _on_data_dir_changed(self) -> None:
        """A new data dir invalidates everything keyed to the old one.

        A plugin selection is a list of filenames that were present in
        one directory; carried into another it is at best misleading and
        at worst a run that loads a fraction of what the label claims.
        The picker rebuilds its list from the field each time it opens,
        so the stale piece is the *selection* (and the label reporting
        it), not the list itself.

        Drop back to "active load order", which re-reads plugins.txt
        against the new directory on the next run or preview.
        """
        new_dir = self.data_dir_edit.text().strip()
        if _same_dir(new_dir, self._last_data_dir):
            return
        self._last_data_dir = new_dir

        had_selection = self._plugin_override is not None
        self._plugin_override = None
        self.plugins_label.setText("(using active load order)")
        log = logging.getLogger(__name__)
        if had_selection:
            log.info("Data dir changed to %s - plugin selection cleared, "
                     "back to the active load order", new_dir or "(unset)")
        else:
            log.info("Data dir changed to %s", new_dir or "(unset)")

        # Report immediately how much of the active load order actually
        # lives there. This is the whole ballgame for a Mod Organizer
        # user: pointing at the Steam install instead of the folder MO2
        # virtualizes silently costs you every mod, and the old symptom
        # was an empty patch twenty minutes later.
        self._warn_if_load_order_absent(new_dir)

        # The preview holds a session built over the old directory.
        if hasattr(self, "preview_pane"):
            self.preview_pane.refresh_on_plugins_change()

    def _warn_if_load_order_absent(self, data_dir_str: str) -> None:
        """Warn when few/none of the active plugins are in `data_dir_str`."""
        if not data_dir_str:
            return
        data_dir = Path(data_dir_str)
        # is_listable_dir, not is_dir(): stat can't see a directory that
        # exists only inside an MO2 mod, and telling the user their data
        # dir "does not exist" when it plainly does would be the most
        # misleading message in the app.
        if not is_listable_dir(data_dir):
            logging.getLogger(__name__).warning(
                "Data dir does not exist: %s", data_dir)
            return
        try:
            active = list(LoadOrder.from_game("tes5", active_only=True).plugins)
        except Exception:
            return
        if not active:
            return
        # is_readable_file, not is_file(): stat can't see MO2's
        # virtual files, and a false alarm here would send the user
        # chasing a data dir that was right all along.
        missing = [n for n in active
                   if not is_readable_file(data_dir / n)]
        if not missing:
            return
        logging.getLogger(__name__).warning(
            "%d of %d active plugins are not in %s. If you are running "
            "under Mod Organizer, set the data dir to the Data folder "
            "inside the game directory MO2 manages (for a Wabbajack list "
            "that is usually <modlist>\\Stock Game\\Data), not the Steam "
            "install.", len(missing), len(active), data_dir)

    def _browse_output_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Select output directory",
            self.output_dir_edit.text() or self.data_dir_edit.text() or "")
        if path:
            self.output_dir_edit.setText(path)

    def _browse_log_file(self) -> None:
        start_dir = (self.log_file_edit.text().strip()
                     or self.output_dir_edit.text().strip()
                     or self.data_dir_edit.text().strip()
                     or "")
        path, _ = QFileDialog.getSaveFileName(
            self, "Log file", start_dir,
            "Log files (*.log);;All files (*)")
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
        # See _warn_if_load_order_absent: is_dir() would slam this modal
        # in the face of an MO2 user whose data dir is perfectly fine.
        if not is_listable_dir(data_dir):
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
            # furrify_schlongs has no GUI surface — SOS handling is a
            # no-op when SOS isn't in the load order, so the toggle was
            # dead weight. CLI's --no-schlongs is still around if needed.
            build_facegen=self.facegen_cb.isChecked(),
            debug=self.debug_cb.isChecked(),
            log_file=self.log_file_edit.text().strip() or None,
            game_data_dir=self.data_dir_edit.text().strip() or None,
            output_dir=self.output_dir_edit.text().strip() or None,
            facegen_limit=facegen_limit,
            facetint_size=self.facetint_size_combo.currentData(),
            preserve_existing=self.skip_furry_cb.isChecked(),
            facegen_throttle=self.throttle_cb.isChecked(),
            # Carried so the logged command line can reproduce a run
            # made against a hand-picked plugin subset. None = active
            # load order, which is also the CLI's default.
            plugin_selection=(list(self._plugin_override)
                              if self._plugin_override is not None else None),
        )

    def _build_load_order(
            self, config: FurrifierConfig) -> Optional[LoadOrder]:
        if self._plugin_override is None:
            return None
        data_dir = (Path(config.game_data_dir) if config.game_data_dir
                    else None)
        return LoadOrder.from_list(
            self._plugin_override, data_dir=data_dir, game_id="tes5")

    def _run_or_cancel_clicked(self) -> None:
        """Dispatch the run-button click by worker state. Idle → start
        a run; running → request cooperative cancel."""
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self.run_button.setEnabled(False)
            self.run_button.setText("Cancelling...")
            self.phase_label.setText("Cancelling...")
        else:
            self._start_run()

    def _start_run(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        config = self._config_from_fields()
        load_order = self._build_load_order(config)

        self.log_text.clear()
        self._install_log_handler(config)
        # Repurpose the button as Cancel for the duration of the run.
        self.run_button.setText("Cancel")
        self.phase_label.setText("Starting...")

        worker = _Worker(config, load_order, cache=self._session_cache,
                         parent=self)
        worker.phase.connect(self.phase_label.setText)
        worker.finished_ok.connect(self._on_finished_ok)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_finished_ok(self) -> None:
        self._remove_log_handler()
        self.run_button.setEnabled(True)
        self.run_button.setText("Run")
        self.phase_label.setText("Done.")
        self._worker = None
        # Keep the cache — Run's session is a valid "already furrified"
        # view of the world that the preview should reuse. The preview
        # worker detects NPCs whose top-of-chain override lives in the
        # shared patch and bakes them as-is (see worker._post_run_npc).

    def _on_failed(self, message: str) -> None:
        self._remove_log_handler()
        self.run_button.setEnabled(True)
        self.run_button.setText("Run")
        self.phase_label.setText("Failed.")
        QMessageBox.critical(self, "Furrifier",
                             f"Furrification failed:\n{message}")
        self._worker = None
        # Failure may have left the patch mid-populated. Drop the cache
        # so the next Load NPCs gets a clean load.
        self._session_cache.invalidate()

    def _on_cancelled(self) -> None:
        self._remove_log_handler()
        self.run_button.setEnabled(True)
        self.run_button.setText("Run")
        self.phase_label.setText("Cancelled.")
        self._worker = None
        # Cancel mid-run leaves the patch in whatever state the
        # cancel checkpoint caught it. Drop the cache so the next
        # Load NPCs / Run sees a clean slate.
        self._session_cache.invalidate()

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
        from .config import resolve_log_path
        resolved = resolve_log_path(config)
        if resolved is not None:
            config.log_file = str(resolved)
        if self._file_handler is None and config.log_file:
            try:
                log_path = Path(config.log_file).resolve()
                ensure_dir(log_path.parent)
                fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
                fh.setLevel(level)
                fh.setFormatter(logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"))
                root.addHandler(fh)
                self._file_handler = fh
                # Confirm in the log pane so the user knows the path
                # was resolved and the file is open. Surfaces silent
                # path-resolution drift (e.g. CWD-relative paths
                # pointing somewhere unexpected).
                logging.getLogger(__name__).info(
                    "Logging to file: %s", log_path)
            except Exception as exc:
                # Broaden from OSError — Path.resolve, mkdir, and
                # FileHandler can raise non-OSError on Windows
                # depending on the path. Log the type so we see what
                # blew up if it ever does.
                logging.getLogger(__name__).warning(
                    "could not open log file %r: %s: %s",
                    config.log_file, type(exc).__name__, exc)
        elif self._file_handler is not None:
            # Already attached — make sure its level matches the
            # current debug setting.
            self._file_handler.setLevel(level)
            if (config.log_file and
                    Path(config.log_file).resolve() !=
                    Path(self._file_handler.baseFilename).resolve()):
                logging.getLogger(__name__).warning(
                    "Log file path changed from %r to %r mid-session; "
                    "still writing to the original — restart the GUI "
                    "to switch.",
                    self._file_handler.baseFilename, config.log_file)

    def _remove_log_handler(self) -> None:
        # Restore the root log level and detach + close the file
        # handler so the log file isn't held open between runs. The
        # next Preview Load / Run reopens it via _install_log_handler.
        if hasattr(self, "_saved_root_level"):
            root = logging.getLogger()
            root.setLevel(self._saved_root_level or logging.INFO)
            self._persistent_handler.setLevel(root.level)
        if self._file_handler is not None:
            root = logging.getLogger()
            root.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None


# --- plugin picker ----------------------------------------------------------


class PluginPickerDialog(QDialog):
    """Modal checkbox list for picking which plugins to run against.

    Lists every *.esp/*.esm/*.esl in the data dir in load-order order.
    Plugins currently marked active in plugins.txt are pre-checked;
    others are unchecked. Checking a plugin automatically pulls in its
    transitive masters (parsed from each plugin's TES4 header).

    Only plugins actually present in the data dir are listed — the
    list is the load order intersected with the directory, not their
    union. Active plugins.txt entries that got dropped are warned
    about in the log and in a banner on the dialog.
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
        # Load-order entries with no matching file in data_dir. Filled
        # by _collect_plugins; the active ones get surfaced below.
        self._absent: list[str] = []

        plugins_in_order = self._collect_plugins(data_dir)
        active = self._active_plugins()
        if initial_selection is not None:
            checked = {p.lower() for p in initial_selection}
        else:
            checked = active

        # An *inactive* plugins.txt entry that isn't on disk is just a
        # stale line and nobody cares. An *active* one is a plugin the
        # game intends to load and we can't see — either the data dir
        # is wrong or the files aren't reachable from this process.
        # Either way the run is going to come up short, so say so now
        # rather than letting the user discover it as "0 NPCs patched".
        self._missing_active = [n for n in self._absent
                                if n.lower() in active]
        if self._missing_active:
            shown = ", ".join(self._missing_active[:8])
            if len(self._missing_active) > 8:
                shown += f", and {len(self._missing_active) - 8} more"
            logging.getLogger(__name__).warning(
                "%d active plugin(s) from plugins.txt are not present in "
                "%s and were left out of the list: %s",
                len(self._missing_active), data_dir, shown)

        self._build_widgets(plugins_in_order, checked)

    def _collect_plugins(self, data_dir: Path) -> list[str]:
        """Ordered plugin list: load-order order, then any extras on disk.

        Intersected with what's actually in `data_dir`. A plugins.txt
        entry we can't see on disk cannot be loaded, so listing it only
        invites a run that reports it missing — which is exactly the
        confusion this list is supposed to prevent. The dropped names
        are kept in `_absent` so the caller can say so out loud
        instead of letting them vanish.
        """
        load_order_names: list[str] = []
        try:
            lo = LoadOrder.from_game("tes5", active_only=False)
            load_order_names = list(lo.plugins)
        except Exception:
            pass

        # os.scandir, not Path.iterdir: a DirEntry answers is_file()
        # from the data the directory enumeration already returned,
        # with no separate stat call. That matters under Mod Organizer,
        # where enumeration sees the merged view of vanilla + mods but
        # stat does not — iterdir().is_file() filtered every modded
        # plugin straight back out of the list it had just found.
        on_disk: list[str] = []
        try:
            with os.scandir(data_dir) as entries:
                for entry in entries:
                    if (Path(entry.name).suffix.lower() in PLUGIN_EXTS
                            and entry.is_file()):
                        on_disk.append(entry.name)
        except OSError:
            pass
        on_disk.sort(key=str.lower)
        on_disk_lower = {name.lower() for name in on_disk}

        present = [n for n in load_order_names if n.lower() in on_disk_lower]
        self._absent = [n for n in load_order_names
                        if n.lower() not in on_disk_lower
                        and n.lower() not in self._exclude]

        seen_lower = {name.lower() for name in present}
        extras = [name for name in on_disk if name.lower() not in seen_lower]
        combined = present + extras
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

        # Absent-but-active plugins get a standing banner rather than a
        # modal — the user needs it while looking at the list, not as
        # something to click past before seeing it.
        if self._missing_active:
            warn = QLabel(
                f"⚠ {len(self._missing_active)} active plugin(s) in "
                f"plugins.txt are not in this data directory and can't "
                f"be loaded. Hover for the list.", self)
            warn.setWordWrap(True)
            # Cap the tooltip — when the data dir is wrong this list is
            # the entire load order, and a 250-line tooltip is a wall,
            # not information. The count above is the real signal.
            names = self._missing_active[:40]
            if len(self._missing_active) > 40:
                names.append(f"... and {len(self._missing_active) - 40} more")
            warn.setToolTip("\n".join(names))
            warn.setObjectName("pluginWarning")
            layout.addWidget(warn)

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
        # No is_file() guard - read_plugin_masters already returns []
        # on any read failure, and the guard could not see MO2's
        # virtual plugins, so master pull-in silently did nothing for
        # every modded plugin in an MO2 load order.
        masters = read_plugin_masters(path)
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


def _parse_gui_args(argv: list[str]) -> tuple[Optional[str], list[str]]:
    """Pull the GUI's own switches out of argv; hand the rest to Qt.

    `--data-dir` exists so a Mod Organizer executable definition can
    launch the GUI already pointed at the Data folder MO2 virtualizes.
    Auto-detection can't find that folder — it goes through the registry
    to the Steam install, which on a Wabbajack stock-game modlist is the
    one Data folder guaranteed not to contain the mods.

    parse_known_args, not parse_args: Qt has its own command-line
    switches (-style, -platform, ...) and swallowing them here would
    break them.
    """
    parser = argparse.ArgumentParser(
        prog="furrify_skyrim_gui",
        description="Skyrim Furrifier (GUI). Most settings live in the "
                    "window; the switches here just set its starting state.")
    parser.add_argument(
        "--data-dir", metavar="DIR",
        help="Skyrim Data directory to start with, instead of the "
             "auto-detected one. Under Mod Organizer this should be the "
             "Data folder inside the game directory MO2 manages.")

    # A windowed PyInstaller build has no stdout/stderr, and argparse
    # writes both --help and its error messages there. Writing to None
    # raises inside argparse, so a single typo in an MO2 executable
    # definition would kill the GUI before it drew a window, with
    # nothing on screen to say why. Capture instead, and start anyway.
    saved = sys.stdout, sys.stderr
    buf = io.StringIO()
    if sys.stdout is None:
        sys.stdout = buf
    if sys.stderr is None:
        sys.stderr = buf
    try:
        args, rest = parser.parse_known_args(argv[1:])
    except SystemExit:
        # --help, or a switch of ours given badly (e.g. --data-dir with
        # no value). Nothing to show in a windowed build, so start with
        # defaults and keep the reason.
        _startup_notes.append(
            "Command line ignored: "
            + (buf.getvalue().strip().replace("\n", " ") or "unparseable"))
        return None, [argv[0]]
    finally:
        sys.stdout, sys.stderr = saved

    # parse_known_args doesn't complain about switches it doesn't know —
    # it hands them to Qt. That silence is a trap here: `--datadir` for
    # `--data-dir` would start with the auto-detected Steam folder and
    # look completely normal, which is the exact failure this switch
    # exists to prevent. Qt's own switches are legitimate leftovers, so
    # note rather than reject.
    leftovers = [a for a in rest if a.startswith("-")]
    if leftovers:
        _startup_notes.append(
            "Command-line switches not recognized by the furrifier, "
            f"passed to Qt: {' '.join(leftovers)}"
            + ("" if args.data_dir else
               " (note --data-dir was not set; check the spelling)"))
    return args.data_dir, [argv[0]] + rest


def main() -> int:
    data_dir, qt_argv = _parse_gui_args(sys.argv)

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

    app = QApplication(qt_argv)
    # QSS needs an absolute URL for the check-tick image. Simple
    # substitution (not str.format — QSS has lots of unrelated
    # curly braces that would trip .format()).
    check_url = _asset_path("check.svg").resolve().as_posix()
    app.setStyleSheet(
        _APP_STYLESHEET.replace("{check_icon}", check_url))
    window = FurrifierWindow(data_dir=data_dir)
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

/* Standing warning banner in the plugin picker — active plugins.txt
   entries that aren't present in the data directory. */
QLabel#pluginWarning {
    color: #E8B45C;
    background-color: #332B1C;
    border: 1px solid #5C4A26;
    border-radius: 3px;
    padding: 5px 7px;
}
"""


if __name__ == "__main__":
    sys.exit(main())
