"""Background worker that owns the FurrificationSession for the
live-preview pane.

Setup (plugin load + race furrification) is ~15-20s on a real load
order; per-NPC bakes are ~1-2s. Both have to run off the GUI thread
so the UI stays responsive.

The worker accepts two kinds of requests:

- `build_session(config)`: lazily creates a FurrificationSession.
  Idempotent when the config hasn't changed since the last build.
  When it has, the previous session is discarded.
- `bake(form_id)`: requires a built session. Resolves the NPC,
  furrifies it against the session, bakes a facegen nif + DDS into
  a temp dir, and emits a signal with the nif path.

Multiple rapid-fire bake requests: only the latest one produces a
usable result. Each request gets a monotonically-increasing ID; the
worker discards intermediate results whose ID isn't the latest.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot

from esplib import LoadOrder, PluginSet
from esplib.record import Record

from ..config import FurrifierConfig
from ..session import (
    FurrificationSession,
    bake_facegen_for,
)
from ..session_cache import SessionCache


log = logging.getLogger("furrifier.preview.worker")


def _log_bake_result(npc: Record, session, nif_path) -> None:
    """Record what the bake actually produced, at DEBUG.

    The preview's bake directory is deleted when the window closes, so
    a headless preview leaves no artifact to inspect afterwards. This
    puts the head-part set and the resolved race into the log, where it
    survives — and names the race, since a race that fails to resolve is
    what silently strips the head-part defaults.
    """
    if not log.isEnabledFor(logging.DEBUG):
        return
    try:
        from ..facegen import base_plugin_for, extract_npc_info
        info = extract_npc_info(npc, session.plugin_set,
                                base_plugin_for(npc, session.patch))
        parts = ", ".join(f"{h['hdpt_edid']}(type={h['hdpt_type']})"
                          for h in info["headparts"])
        log.debug("Preview bake %s -> %s", npc.editor_id, nif_path)
        log.debug("  race=%s  headparts=%d [%s]  tints=%d",
                  info.get("race_edid"), len(info["headparts"]),
                  parts or "<none>", len(info.get("tints") or []))
    except Exception:
        # Diagnostics must never take the preview down with them.
        log.debug("Preview bake diagnostics failed", exc_info=True)


def _resolve_face_npc(npc: Record, plugin_set: PluginSet) -> Record:
    """Walk the TPLT chain until we hit the NPC whose face the game
    actually uses.

    NPCs with ACBS `template_flags.Traits` set inherit appearance
    (race, headparts, sliders, tints) from their TPLT target; their
    own face data is usually empty or placeholder (e.g. a "NoScar"
    marker with nothing else). Baking such a shell yields a preview
    with nothing but default eyes, which is what we saw for
    DLC2WaterStoneSailor1. Resolving up the chain gives us the face
    the game would actually render.

    Bails out on cycles (defensive — shouldn't happen in vanilla) and
    on TPLTs that point at leveled lists (LVLN) rather than NPCs —
    those pick a face at runtime from the list and there's no single
    face to preview.
    """
    current = npc
    visited: set[tuple] = set()
    while True:
        key = (
            current.plugin.file_path.name if current.plugin else "",
            int(current.form_id),
        )
        if key in visited:
            break
        visited.add(key)
        try:
            if not current["ACBS"]["template_flags"].Traits:
                break
        except Exception:
            break
        tplt = plugin_set.resolve_reference(current, "TPLT")
        if tplt is None or tplt.signature != "NPC_":
            # Broken ref or a LVLN template — can't follow further.
            break
        current = tplt
    return current


class PreviewWorker(QObject):
    """QObject that runs on its own QThread and owns the session.

    Outgoing signals (GUI-thread connections):
      - session_building(): setup is starting.
      - session_ready(): setup finished; bake requests now possible.
      - session_failed(str): setup hit an error.
      - bake_ready(int, str): request_id + absolute path to baked nif.
      - bake_failed(int, str): request_id + error message.
    """

    session_building = Signal()
    session_ready = Signal()
    session_failed = Signal(str)
    bake_ready = Signal(int, str, str)  # request_id, nif_path, dds_path_or_empty
    bake_failed = Signal(int, str)

    def __init__(self, cache: SessionCache,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        # Shared with the Run worker via the main window — a preview
        # build populates it, a subsequent Run reuses the plugin load.
        self._cache = cache
        self._session: Optional[FurrificationSession] = None
        self._config: Optional[FurrifierConfig] = None
        self._temp_root: Optional[Path] = None
        # Each bake request gets a monotonic ID. The GUI records the
        # latest ID it issued; stale completions can be ignored.
        self._latest_request_id: int = 0

    # ----- incoming slots (from GUI thread via QueuedConnection) -----------

    @Slot(object, object)
    def build_session(self, config: FurrifierConfig,
                      load_order: Optional[LoadOrder] = None) -> None:
        """Build (or rebuild) the session via the shared cache.

        The cache itself decides whether the call is a no-op (full
        match), a scheme-only rebuild (~1-2s), or a full cold load
        (~15s). Building here only emits ``session_building`` when
        real work would happen, so repeat clicks with the same config
        don't flash the status label.
        """
        self.session_building.emit()
        try:
            self._session = self._cache.get_or_build_session(
                config, load_order=load_order)
            # Keep the live config. session.config is whatever the
            # session was *built* with, and options that don't affect
            # setup — Skip furry NPCs among them — aren't in the cache
            # key, so a cached session carries a stale copy of them.
            self._config = config

            if self._temp_root is None:
                self._temp_root = Path(
                    tempfile.mkdtemp(prefix="furrifier_preview_bake_"))
            self.session_ready.emit()
        except Exception as exc:
            log.exception("Session build failed: %s", exc)
            self._session = None
            self.session_failed.emit(str(exc))

    @Slot(int, int)
    def bake(self, request_id: int, form_id: int) -> None:
        """Furrify + bake one NPC. Caller passes a request_id it
        tracks; the emitted result carries the same ID so the GUI can
        discard stale completions."""
        # Register this as the latest in-flight request. The GUI should
        # supply strictly increasing IDs; discard anything older.
        self._latest_request_id = max(self._latest_request_id, request_id)

        if self._session is None:
            self.bake_failed.emit(request_id, "No session — click Preview first")
            return

        try:
            chain = self._session.plugin_set.get_override_chain(form_id)
            if not chain:
                self.bake_failed.emit(
                    request_id, f"Form ID {form_id:08X} not resolvable")
                return
            npc = chain[-1]

            # If this NPC inherits its face from a template, furrify and
            # bake from the template instead — otherwise we get a shell
            # NPC with empty PNAMs and the preview ends up as just eyes.
            face_npc = _resolve_face_npc(npc, self._session.plugin_set)
            if face_npc is not npc:
                log.debug(
                    "Preview: %s uses template traits — baking from %s",
                    npc.editor_id, face_npc.editor_id)

            patched = self._resolve_preview_record(face_npc)
            if patched is None:
                self.bake_failed.emit(
                    request_id,
                    f"{npc.editor_id}: scheme doesn't furrify this NPC "
                    f"(wrong race, or CharGen preset)")
                return

            # Before emitting, check we're still the latest request.
            # A newer request already overwrote us; the result would
            # paint stale into the viewer.
            if request_id != self._latest_request_id:
                return

            assert self._temp_root is not None
            nif_path, dds_path = bake_facegen_for(
                patched, self._session, out_dir=self._temp_root)
            _log_bake_result(patched, self._session, nif_path)

            self.bake_ready.emit(
                request_id, str(nif_path),
                str(dds_path) if dds_path is not None else "")
        except Exception as exc:
            log.exception("Bake failed: %s", exc)
            self.bake_failed.emit(request_id, str(exc))

    def _resolve_preview_record(self, npc: Record) -> Optional[Record]:
        """The record the preview should bake, matching what a Run would
        produce for this NPC. Returns None if the scheme doesn't cover it.

        Three cases, in order:

        1. Already in our own patch — a previous Run furrified it. Use it
           as-is; re-furrifying a furry race would fail to match.
        2. Already furrified by an *earlier* patch left in the load
           order. Then the Skip-furry-NPCs setting decides, exactly as it
           does in a Run: preserve means show that patch's NPC unchanged,
           otherwise walk back to the topmost non-furry record and
           re-derive under the current scheme.
        3. Not furrified — furrify it normally.

        Case 2 previously fell into the plain furrify path, where
        determine_npc_race can't match an already-furry RNAM, so the
        preview just refused with "scheme doesn't furrify this NPC".
        """
        from ..context import find_pre_furry_record, is_furrified

        session = self._session
        if npc.plugin is session.patch:
            return npc

        ctx = session.context
        if is_furrified(session.plugin_set, npc, ctx.furrifier_patch_names()):
            preserve = (self._config or session.config).preserve_existing
            if preserve:
                log.debug("Preview: %s is already furrified and "
                          "preserve-existing is on — showing it as-is",
                          npc.editor_id)
                return npc
            pre = find_pre_furry_record(
                session.plugin_set, npc, ctx.furrifier_patch_names())
            if pre is None:
                log.debug("Preview: %s is furrified but has no non-furry "
                          "record to re-derive from", npc.editor_id)
                return None
            log.debug("Preview: re-deriving %s from its %s record",
                      npc.editor_id,
                      pre.plugin.file_path.name if pre.plugin else "<?>")
            npc = pre

        return ctx.furrify_npc(npc)

    # ----- cleanup ---------------------------------------------------------

    def shutdown(self) -> None:
        """Remove the bake-temp dir. Called when the window closes."""
        import shutil
        if self._temp_root is not None:
            shutil.rmtree(self._temp_root, ignore_errors=True)
            self._temp_root = None


@dataclass
class RequestTracker:
    """Tiny helper the GUI side uses to issue monotonically-increasing
    request IDs and recognize stale completions.

    This is what lets the user mash buttons without waiting: only the
    newest request's result ends up on screen.
    """
    _counter: int = field(default=0)

    def next_id(self) -> int:
        self._counter += 1
        return self._counter

    def is_current(self, request_id: int) -> bool:
        return request_id == self._counter
