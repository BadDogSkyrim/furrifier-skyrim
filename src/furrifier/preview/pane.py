"""Live preview pane: top-level widget that combines picker + worker +
3D viewer into a single drop-in QWidget for the main window's split.

Lifecycle:
  1. Pane is created with a callback for getting the current config.
  2. First user interaction (picking an NPC) triggers session build on
     the background worker. Status label shows "Loading plugins...".
  3. Once session is ready, NPC is baked and the scene widget updates.
  4. Subsequent picks reuse the session; each bake is ~1-2s.
  5. When the config changes (e.g. scheme), the next pick rebuilds.

The pane doesn't own the FurrifierConfig — the main window does.
The pane calls `config_provider()` whenever it needs current config,
so scheme changes propagate naturally.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from esplib import LoadOrder

from ..config import FurrifierConfig
from .npc_picker import NpcEntry, NpcPickerWidget
from .scene_widget import FacegenSceneWidget
from .worker import PreviewWorker, RequestTracker


log = logging.getLogger("furrifier.preview.pane")


ConfigProvider = Callable[[], FurrifierConfig]
LoadOrderProvider = Callable[[], Optional[LoadOrder]]


@dataclass
class _HistoryEntry:
    """One slot in the browser-style back/forward history.

    `nif_path is None` means the entry is dirty (scheme changed
    since it was baked, or it hasn't been baked yet) and will be
    re-baked on navigation.
    """
    form_id: int
    nif_path: Optional[Path] = None
    dds_path: Optional[Path] = None


class PreviewPane(QWidget):
    """Vertical stack: picker + status + 3D viewer.

    Owns its own background worker (QThread). The main window passes
    a `config_provider` callable for on-demand access to the current
    `FurrifierConfig`; picks a new NPC → worker rebuilds session if
    config changed → bakes → scene widget displays.
    """

    # Cross-thread dispatch uses plain signals rather than
    # QMetaObject.invokeMethod — Qt's meta-object system doesn't know
    # how to unpack PyObject-typed Q_ARGs at runtime, whereas signals
    # carry Python types natively across QueuedConnection.
    _dispatch_build = Signal(object, object)  # config, load_order
    _dispatch_bake = Signal(int, int)         # request_id, form_id

    def __init__(self, config_provider: ConfigProvider,
                 load_order_provider: Optional[LoadOrderProvider] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config_provider = config_provider
        # Optional — when None, the worker's setup_session falls back
        # to LoadOrder.from_game(active_only=True).
        self._load_order_provider = load_order_provider
        self._request_tracker = RequestTracker()
        # Tracks which NPC is currently visible in the viewer, so
        # scheme changes can re-bake it without forcing the user to
        # pick again. None = nothing displayed yet.
        self._last_form_id: Optional[int] = None
        # When set, _on_session_ready re-dispatches a bake for this
        # form_id after the picker repopulates. Used by
        # refresh_on_scheme_change to preserve the visible NPC
        # across a session rebuild.
        self._pending_rebake_form_id: Optional[int] = None

        # Browser-style back/forward history. _history_pos points at
        # the currently-displayed entry; forward-of-current is still
        # navigable until the user picks a new NPC, at which point
        # the forward tail gets truncated (standard browser semantics).
        # Cap is generous — baked nifs are small (~1-10 MB each) so
        # holding 20 of them in a temp dir is fine, and it's plenty
        # of depth for an iterate-scheme-then-back-up workflow.
        self._history: list[_HistoryEntry] = []
        self._history_pos: int = -1
        self._history_cap: int = 20
        # Suppresses the "append to history" side of bake_ready while
        # we're navigating to a cached entry that happens to be dirty
        # — that bake updates the existing entry rather than creating
        # a new one.
        self._rebaking_history_pos: Optional[int] = None
        # When True, the next set_nif call will snap the camera back
        # to its default framing; when False, orbit state is preserved
        # across the display. Fresh picks from the picker set this
        # True; back/forward navigation leaves it False so the user's
        # chosen camera angle persists through their comparison session.
        self._reset_camera_next: bool = True

        self.load_button = QPushButton("Load NPCs", self)
        self.load_button.clicked.connect(self._on_load_clicked)
        # Back / Forward nav over the preview history. Small fixed-
        # width so they don't dominate the picker row. Unicode arrows
        # render fine in Qt's default font.
        self.back_button = QPushButton("◀", self)
        self.back_button.setFixedWidth(32)
        self.back_button.setEnabled(False)
        self.back_button.setToolTip("Previous NPC in preview history")
        self.back_button.clicked.connect(self._on_back)
        self.forward_button = QPushButton("▶", self)
        self.forward_button.setFixedWidth(32)
        self.forward_button.setEnabled(False)
        self.forward_button.setToolTip("Next NPC in preview history")
        self.forward_button.clicked.connect(self._on_forward)
        self.picker = NpcPickerWidget(self)
        self.picker.setEnabled(False)  # enabled once NPCs loaded
        self.scene = FacegenSceneWidget(self)
        # 3:4 portrait aspect — heads are taller than wide. Scene
        # uses heightForWidth to request 4/3 height per pixel of
        # width; the outer layout cooperates via setHeightForWidth.
        from PySide6.QtWidgets import QSizePolicy as _SP
        sp = _SP(_SP.Policy.Expanding, _SP.Policy.Expanding)
        sp.setHeightForWidth(True)
        self.scene.setSizePolicy(sp)
        self.status_label = QLabel("Click 'Load NPCs' to begin.", self)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Editor-id + headparts readout under the viewer — useful
        # for confirming which parts the furrifier actually chose.
        self.headparts_label = QLabel("", self)
        self.headparts_label.setWordWrap(True)
        self.headparts_label.setStyleSheet(
            "QLabel { color: #888; font-size: 9pt; }")
        self._last_nif_path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        # Load button at the top (it's the first thing the user
        # interacts with); picker row with back/forward nav below;
        # scene fills the remaining space; headparts list footer.
        layout.addWidget(self.load_button)
        nav_row = QHBoxLayout()
        nav_row.addWidget(self.back_button)
        nav_row.addWidget(self.picker, stretch=1)
        nav_row.addWidget(self.forward_button)
        layout.addLayout(nav_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.scene, stretch=1)
        layout.addWidget(self.headparts_label)

        # Background worker + thread. Qt-native pattern: QObject
        # living on a QThread; slots dispatched via QueuedConnection.
        self._thread = QThread(self)
        self._worker = PreviewWorker()
        self._worker.moveToThread(self._thread)
        self._thread.start()

        self._worker.session_building.connect(self._on_session_building)
        self._worker.session_ready.connect(self._on_session_ready)
        self._worker.session_failed.connect(self._on_session_failed)
        self._worker.bake_ready.connect(self._on_bake_ready)
        self._worker.bake_failed.connect(self._on_bake_failed)

        # Queued signals → worker slots. Qt routes these across the
        # thread boundary automatically because the target lives on a
        # different QThread.
        self._dispatch_build.connect(self._worker.build_session)
        self._dispatch_bake.connect(self._worker.bake)

        self.picker.npc_selected.connect(self._on_npc_picked)

    # ----- user actions ----------------------------------------------------

    def _on_load_clicked(self) -> None:
        """Kick off session setup in the worker. This also populates
        the NPC picker once the session is ready."""
        self.load_button.setEnabled(False)
        lo = (self._load_order_provider()
              if self._load_order_provider is not None else None)
        self._dispatch_build.emit(self._config_provider(), lo)

    def _on_npc_picked(self, form_id: int) -> None:
        """User committed an NPC choice — dispatch a bake request.

        If the picked NPC is already somewhere in history, jump the
        cursor to that existing entry rather than appending a
        duplicate. That keeps the back/forward chain free of repeats
        and reuses any cached bake for that NPC. Only a genuinely
        new NPC truncates forward history and appends.
        """
        # Dedupe against existing history so the same NPC never
        # appears twice in the back/forward chain.
        for i, entry in enumerate(self._history):
            if entry.form_id == form_id:
                if i == self._history_pos and entry.nif_path is not None:
                    # Already displayed + cached — nothing to do.
                    return
                self._history_pos = i
                self._navigate_to_current()
                return

        # Genuinely new NPC — preserve camera across the pick so
        # comparing faces from a fixed angle works. Truncate forward
        # history and append a dirty entry.
        del self._history[self._history_pos + 1:]
        self._history.append(_HistoryEntry(form_id=form_id))
        # Cap history size; drop from the oldest side.
        if len(self._history) > self._history_cap:
            drop = len(self._history) - self._history_cap
            self._history = self._history[drop:]
        self._history_pos = len(self._history) - 1
        self._update_nav_buttons()
        self._dispatch_bake_for_current()

    def _dispatch_bake_for_current(self) -> None:
        """Kick off a bake for whatever NPC is at the current history
        position. Used by _on_npc_picked and the back/forward handlers
        when they land on a dirty entry."""
        if self._history_pos < 0:
            return
        entry = self._history[self._history_pos]
        self._last_form_id = entry.form_id
        request_id = self._request_tracker.next_id()
        self.status_label.setText(
            f"Baking {entry.form_id:08X}... (request #{request_id})")
        self.scene.set_busy(True, "baking…")
        self._dispatch_bake.emit(request_id, entry.form_id)

    def _on_back(self) -> None:
        if self._history_pos <= 0:
            return
        self._history_pos -= 1
        self._navigate_to_current()

    def _on_forward(self) -> None:
        if self._history_pos >= len(self._history) - 1:
            return
        self._history_pos += 1
        self._navigate_to_current()

    def _navigate_to_current(self) -> None:
        """Show whatever's at the current history position. If the
        entry has a cached bake, display it directly (no re-bake);
        if it's dirty, dispatch a fresh bake."""
        self._update_nav_buttons()
        entry = self._history[self._history_pos]
        self._last_form_id = entry.form_id
        if entry.nif_path is not None and entry.nif_path.is_file():
            # Cached hit — load the scene without going through the
            # worker. Instant.
            config = self._config_provider()
            data_dir = (Path(config.game_data_dir)
                        if config.game_data_dir else None)
            if data_dir is None:
                self.status_label.setText(
                    "No data_dir configured — can't resolve textures")
                return
            self._display_label_for(entry.form_id, entry.nif_path)
            preserve = not self._reset_camera_next
            self._reset_camera_next = False
            try:
                self.scene.set_nif(entry.nif_path, data_dir,
                                   facetint_path=entry.dds_path,
                                   preserve_camera=preserve)
            except Exception as exc:
                log.exception("Scene load failed: %s", exc)
                self.status_label.setText(f"Scene load failed: {exc}")
            self._update_headparts_label(entry.nif_path)
            return
        # Dirty entry — re-bake to populate it.
        self._dispatch_bake_for_current()

    def _update_nav_buttons(self) -> None:
        self.back_button.setEnabled(self._history_pos > 0)
        self.forward_button.setEnabled(
            self._history_pos < len(self._history) - 1)

    def _display_label_for(self, form_id: int,
                            nif_path: Path) -> None:
        edid = self._editor_id_for(form_id)
        self.status_label.setText(
            f"{edid} ({form_id:08X})" if edid else nif_path.name)

    def refresh_on_scheme_change(self) -> None:
        """Scheme changed → session cache-key mismatches → worker
        rebuilds races/FLSTs/presets over the cached plugin set.
        Re-bakes the current NPC after the new picker populates."""
        self._refresh_preserving_current_npc()

    def refresh_on_plugins_change(self) -> None:
        """Plugin selection changed → load-order fingerprint mismatches
        → worker re-loads plugins + everything downstream.
        Re-bakes the current NPC if it's still furrifiable."""
        self._refresh_preserving_current_npc()

    def _refresh_preserving_current_npc(self) -> None:
        if self._worker._session is None:  # noqa: SLF001
            return
        # Re-enable the Load button for visual continuity, then
        # immediately dispatch — user doesn't have to click it.
        self.load_button.setEnabled(True)
        # Every cached bake is now out of date relative to the new
        # scheme. Mark them dirty in place so back/forward still
        # works but each navigation re-bakes.
        for entry in self._history:
            entry.nif_path = None
            entry.dds_path = None
        self._pending_rebake_form_id = self._last_form_id
        self._on_load_clicked()

    # ----- worker signal handlers ------------------------------------------

    def _on_session_building(self) -> None:
        self.status_label.setText(
            "Loading plugins + furrifying races… (~15-20s on first load)")
        self.picker.setEnabled(False)

    def _on_session_ready(self) -> None:
        """Populate the picker from plugin_set once setup finishes."""
        self.status_label.setText("Ready — pick an NPC.")
        # Session is built; user doesn't need to re-press Load. If
        # they change the scheme, refresh_on_scheme_change will
        # re-enable and re-fire this path.
        self.load_button.setEnabled(False)
        self.picker.setEnabled(True)

        entries = self._collect_npc_entries()
        self.picker.set_entries(entries)
        self.status_label.setText(
            f"Ready — {len(entries)} NPCs available. Pick one.")

        # If a scheme-change refresh is in-flight, re-bake the NPC
        # that was on screen before — as long as the new scheme still
        # considers it furrifiable.
        if self._pending_rebake_form_id is not None:
            form_id = self._pending_rebake_form_id
            self._pending_rebake_form_id = None
            if any(e.form_id == form_id for e in entries):
                self._on_npc_picked(form_id)
            else:
                self.status_label.setText(
                    f"NPC {form_id:08X} isn't furrifiable under the "
                    f"new scheme — pick another.")

    def _on_session_failed(self, message: str) -> None:
        self.status_label.setText(f"Session setup failed: {message}")
        self.load_button.setEnabled(True)

    def _on_bake_ready(self, request_id: int, nif_path: str,
                       dds_path: str) -> None:
        if not self._request_tracker.is_current(request_id):
            # Stale — the user clicked another NPC while this was running.
            log.debug("Discarding stale bake result #%d", request_id)
            return
        self._last_nif_path = Path(nif_path)
        nif = Path(nif_path)
        dds = Path(dds_path) if dds_path else None
        # Store the result against the currently-active history entry.
        # (That's always the one we just baked for, whether the bake
        # was triggered by a fresh pick or by navigating to a dirty
        # entry.)
        if 0 <= self._history_pos < len(self._history):
            entry = self._history[self._history_pos]
            if entry.form_id == self._last_form_id:
                entry.nif_path = nif
                entry.dds_path = dds
        edid = self._editor_id_for(self._last_form_id)
        label = (f"{edid} ({self._last_form_id:08X})"
                 if edid else nif.name)
        self.status_label.setText(label)
        self._update_headparts_label(nif)
        config = self._config_provider()
        data_dir = Path(config.game_data_dir) if config.game_data_dir else None
        if data_dir is None:
            self.scene.set_busy(False)
            self.status_label.setText(
                "No data_dir configured — can't resolve textures")
            return
        try:
            tint_path = Path(dds_path) if dds_path else None
            preserve = not self._reset_camera_next
            self._reset_camera_next = False
            self.scene.set_nif(Path(nif_path), data_dir,
                               facetint_path=tint_path,
                               preserve_camera=preserve)
        except Exception as exc:
            log.exception("Scene load failed: %s", exc)
            self.status_label.setText(f"Scene load failed: {exc}")
        finally:
            self.scene.set_busy(False)

    def _on_bake_failed(self, request_id: int, message: str) -> None:
        if not self._request_tracker.is_current(request_id):
            return
        self.scene.set_busy(False)
        self.status_label.setText(f"Bake failed: {message}")

    # ----- helpers ---------------------------------------------------------

    def _editor_id_for(self, form_id: Optional[int]) -> Optional[str]:
        if form_id is None:
            return None
        for entry in self.picker.entries():
            if entry.form_id == form_id:
                return entry.editor_id
        return None

    def _update_headparts_label(self, nif_path: Path) -> None:
        """Read shape names out of the freshly-baked nif and dump
        them under the viewer. Each shape's `name` is the HDPT
        editor id our bake stamped in — useful for confirming which
        headparts the furrifier picked for this NPC."""
        import sys
        pynifly_dev = r"C:\Modding\PyNifly\io_scene_nifly"
        if pynifly_dev not in sys.path:
            sys.path.insert(0, pynifly_dev)
        try:
            from pyn.pynifly import NifFile
            nif = NifFile(str(nif_path))
            names = [s.name for s in nif.shapes]
        except Exception as exc:
            log.debug("Couldn't read shape names for label: %s", exc)
            self.headparts_label.setText("")
            return
        self.headparts_label.setText(
            "Headparts: " + ", ".join(sorted(names)))

    def _collect_npc_entries(self) -> list[NpcEntry]:
        """Pull the list of *furrifiable* NPCs from the worker's session.

        Filters to NPCs whose race is in the scheme (via
        `context.determine_npc_race` returning non-None) — previewing
        a character the furrifier can't touch isn't useful. Dedupe by
        object_index so override chains don't show the same NPC twice.

        Stores AbsoluteFormIDs (load-order-relative) rather than the
        raw `npc.form_id` which is LocalFormID (indexed into the
        defining plugin's master list). The worker's `bake` slot
        passes the form_id to `plugin_set.get_override_chain`, which
        keys on AbsoluteFormID — Dragonborn NPCs (e.g. 0x04xxxxxx
        locally, 0x02xxxxxx absolute) would otherwise not resolve.
        """
        session = self._worker._session  # noqa: SLF001
        if session is None:
            return []
        furry = session.context

        seen_ids: set[int] = set()
        entries: list[NpcEntry] = []
        for plugin in session.plugin_set:
            for npc in plugin.get_records_by_signature("NPC_"):
                abs_fid = plugin.normalize_form_id(npc.form_id)
                obj_id = int(abs_fid) & 0x00FFFFFF
                if obj_id in seen_ids:
                    continue
                seen_ids.add(obj_id)
                # Scheme filter — skips NPCs whose race has no
                # furry assignment (child races, creature-only races,
                # etc.). determine_npc_race is cheap: just a race
                # FormID lookup + dict hits.
                try:
                    if furry.determine_npc_race(npc) is None:
                        continue
                except Exception:
                    continue
                edid = npc.editor_id or f"NPC_{obj_id:06X}"
                entries.append(NpcEntry(
                    form_id=int(abs_fid), editor_id=edid))
        entries.sort(key=lambda e: e.editor_id.lower())
        return entries

    # ----- lifecycle -------------------------------------------------------

    def shutdown(self) -> None:
        """Called by the main window on close. Tears down the worker
        thread cleanly."""
        self._worker.shutdown()
        self._thread.quit()
        self._thread.wait(2000)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)
