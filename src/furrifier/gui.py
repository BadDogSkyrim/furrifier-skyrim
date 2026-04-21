"""Furrifier GUI.

A customtkinter window wrapping the CLI switches. The actual pipeline
runs in a worker thread; log records and phase updates flow back to the
UI through a queue.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional

import customtkinter as ctk
from PIL import Image

from esplib import LoadOrder, find_game_data
from esplib.record import Record
from esplib.utils import BinaryReader

from .config import FurrifierConfig
from .main import run_furrification


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


SCHEMES = ["all_races", "cats_dogs", "legacy", "user"]


def _asset_path(name: str) -> Path:
    """Locate an asset file in dev mode or inside a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "furrifier" / "assets" / name  # type: ignore[attr-defined]
    return Path(__file__).parent / "assets" / name


class _QueueLogHandler(logging.Handler):
    """Logging handler that pushes formatted records onto a queue."""

    def __init__(self, q: "queue.Queue[tuple[str, str]]"):
        super().__init__()
        self._queue = q
        self.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put(("log", self.format(record)))
        except Exception:
            self.handleError(record)


class FurrifierWindow(ctk.CTk):
    """Main furrifier GUI window."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Skyrim Furrifier")
        self.geometry("820x820")
        self.minsize(720, 620)

        self._queue: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._log_handler: Optional[_QueueLogHandler] = None
        self._banner_image: Optional[ctk.CTkImage] = None
        # None = use auto load order from plugins.txt; a list = explicit
        # selection from the plugin picker dialog.
        self._plugin_override: Optional[list[str]] = None

        self._apply_icon()
        self._build_widgets()

    def _apply_icon(self) -> None:
        # Title-bar icon on Windows needs .ico via iconbitmap(); taskbar
        # / Alt-Tab uses the PhotoImage set via iconphoto(). Do both so
        # the icon shows up in every surface.
        try:
            ico_path = _asset_path("furrifier.ico")
            if ico_path.is_file():
                self.iconbitmap(default=str(ico_path))
                self.iconbitmap(str(ico_path))
        except Exception:
            pass
        try:
            png_path = _asset_path("icon_256.png")
            if png_path.is_file():
                self._icon_photo = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    # --- layout -----------------------------------------------------------

    def _build_widgets(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Banner
        banner_path = _asset_path("banner.png")
        if banner_path.is_file():
            pil = Image.open(banner_path)
            self._banner_image = ctk.CTkImage(
                light_image=pil, dark_image=pil, size=pil.size)
            banner_label = ctk.CTkLabel(self, image=self._banner_image, text="")
            banner_label.grid(row=0, column=0, padx=0, pady=0, sticky="ew")

        form = ctk.CTkFrame(self)
        form.grid(row=1, column=0, padx=12, pady=(12, 6), sticky="ew")
        form.grid_columnconfigure(1, weight=1)

        # Scheme
        ctk.CTkLabel(form, text="Scheme:").grid(
            row=0, column=0, padx=(10, 8), pady=8, sticky="w")
        self.scheme_var = tk.StringVar(value="all_races")
        ctk.CTkOptionMenu(form, values=SCHEMES, variable=self.scheme_var).grid(
            row=0, column=1, columnspan=2, padx=(0, 10), pady=8, sticky="w")

        # Patch filename
        ctk.CTkLabel(form, text="Patch file:").grid(
            row=1, column=0, padx=(10, 8), pady=8, sticky="w")
        self.patch_var = tk.StringVar(value="YASNPCPatch.esp")
        ctk.CTkEntry(form, textvariable=self.patch_var).grid(
            row=1, column=1, padx=(0, 8), pady=8, sticky="ew")

        # Data dir — read-side. Source of masters, mods, textures, BSAs.
        # Prefilled with auto-detected game Data folder.
        ctk.CTkLabel(form, text="Data dir:").grid(
            row=2, column=0, padx=(10, 8), pady=8, sticky="w")
        detected = find_game_data('tes5')
        self.data_dir_var = tk.StringVar(value=str(detected) if detected else "")
        ctk.CTkEntry(form, textvariable=self.data_dir_var,
                     placeholder_text="(not auto-detected)").grid(
            row=2, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Browse...", width=120,
                      command=self._browse_data_dir).grid(
            row=2, column=2, padx=(0, 10), pady=8)

        # Output dir — write-side. Patch.esp and FaceGenData tree go
        # here. Blank = same as Data dir.
        ctk.CTkLabel(form, text="Output dir:").grid(
            row=3, column=0, padx=(10, 8), pady=8, sticky="w")
        self.output_dir_var = tk.StringVar(value="")
        ctk.CTkEntry(form, textvariable=self.output_dir_var,
                     placeholder_text="(same as Data dir)").grid(
            row=3, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Browse...", width=120,
                      command=self._browse_output_dir).grid(
            row=3, column=2, padx=(0, 10), pady=8)

        # Plugins: "Edit plugins..." button + selected-count label.
        # Overrides LoadOrder.from_game(active_only=True) for this run.
        ctk.CTkLabel(form, text="Plugins:").grid(
            row=4, column=0, padx=(10, 8), pady=8, sticky="w")
        self.plugins_summary_var = tk.StringVar(value="(using active load order)")
        ctk.CTkLabel(form, textvariable=self.plugins_summary_var,
                     anchor="w").grid(
            row=4, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Edit plugins...", width=120,
                      command=self._open_plugin_picker).grid(
            row=4, column=2, padx=(0, 10), pady=8)

        # Log file
        ctk.CTkLabel(form, text="Log file:").grid(
            row=5, column=0, padx=(10, 8), pady=8, sticky="w")
        self.log_file_var = tk.StringVar(value="")
        ctk.CTkEntry(form, textvariable=self.log_file_var,
                     placeholder_text="(optional)").grid(
            row=5, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Browse...", width=120,
                      command=self._browse_log_file).grid(
            row=5, column=2, padx=(0, 10), pady=8)

        # Profile file (cProfile dump) — optional. Blank = no profiling.
        ctk.CTkLabel(form, text="Profile file:").grid(
            row=6, column=0, padx=(10, 8), pady=8, sticky="w")
        self.profile_file_var = tk.StringVar(value="")
        ctk.CTkEntry(form, textvariable=self.profile_file_var,
                     placeholder_text="(optional — cProfile output)").grid(
            row=6, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Browse...", width=120,
                      command=self._browse_profile_file).grid(
            row=6, column=2, padx=(0, 10), pady=8)

        # Option checkboxes
        opts = ctk.CTkFrame(self)
        opts.grid(row=2, column=0, padx=12, pady=6, sticky="ew")
        self.armor_var = tk.BooleanVar(value=True)
        self.male_var = tk.BooleanVar(value=True)
        self.female_var = tk.BooleanVar(value=True)
        self.schlongs_var = tk.BooleanVar(value=True)
        self.facegen_var = tk.BooleanVar(value=True)
        self.debug_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Furrify armor",
                        variable=self.armor_var).grid(row=0, column=0, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Male NPCs",
                        variable=self.male_var).grid(row=0, column=1, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Female NPCs",
                        variable=self.female_var).grid(row=0, column=2, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Schlongs (SOS)",
                        variable=self.schlongs_var).grid(row=0, column=3, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Build FaceGen",
                        variable=self.facegen_var).grid(row=0, column=4, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Debug logging",
                        variable=self.debug_var).grid(row=0, column=5, padx=10, pady=8, sticky="w")

        # Log pane
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=3, column=0, padx=12, pady=6, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        self.log_text = ctk.CTkTextbox(log_frame, wrap="word",
                                       font=("Consolas", 11))
        self.log_text.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")
        self.log_text.configure(state="disabled")

        # Bottom bar: phase label + run button. No progress bar — the
        # customtkinter indeterminate bar's animation burned ~175s of
        # CPU across a 100-NPC run (profiled 2026-04-21) because it
        # redraws at high frequency on a timer. The phase label (which
        # updates every NPC via the worker's progress callback) is
        # enough to show motion.
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=4, column=0, padx=12, pady=(6, 12), sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)
        self.phase_var = tk.StringVar(value="Ready.")
        ctk.CTkLabel(bottom, textvariable=self.phase_var, anchor="w").grid(
            row=0, column=0, padx=10, pady=8, sticky="ew")
        self.run_button = ctk.CTkButton(bottom, text="Run", width=120,
                                        command=self._start_run)
        self.run_button.grid(row=0, column=1, padx=10, pady=8)

    # --- actions ---------------------------------------------------------

    def _browse_data_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Select Skyrim Data directory",
            initialdir=self.data_dir_var.get() or None,
        )
        if path:
            self.data_dir_var.set(path)

    def _browse_output_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Select output directory",
            initialdir=(self.output_dir_var.get()
                        or self.data_dir_var.get()
                        or None),
        )
        if path:
            self.output_dir_var.set(path)

    def _open_plugin_picker(self) -> None:
        data_dir_str = self.data_dir_var.get().strip()
        if not data_dir_str:
            messagebox.showerror(
                "Plugins",
                "Set a data directory before picking plugins.")
            return
        data_dir = Path(data_dir_str)
        if not data_dir.is_dir():
            messagebox.showerror(
                "Plugins", f"Data directory not found: {data_dir}")
            return

        dialog = PluginPickerDialog(
            self, data_dir=data_dir,
            initial_selection=self._plugin_override)
        self.wait_window(dialog)

        if dialog.result is None:
            return  # cancelled
        self._plugin_override = dialog.result
        self.plugins_summary_var.set(
            f"{len(self._plugin_override)} plugin(s) selected")


    def _browse_log_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Log file",
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if path:
            self.log_file_var.set(path)

    def _browse_profile_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="cProfile output file",
            defaultextension=".prof",
            filetypes=[("Profile files", "*.prof"), ("All files", "*.*")],
        )
        if path:
            self.profile_file_var.set(path)

    def _config_from_fields(self) -> FurrifierConfig:
        patch = self.patch_var.get().strip() or "YASNPCPatch.esp"
        if Path(patch).suffix.lower() not in (".esp", ".esm", ".esl"):
            patch += ".esp"
        return FurrifierConfig(
            patch_filename=patch,
            race_scheme=self.scheme_var.get(),
            furrify_armor=self.armor_var.get(),
            furrify_npcs_male=self.male_var.get(),
            furrify_npcs_female=self.female_var.get(),
            furrify_schlongs=self.schlongs_var.get(),
            build_facegen=self.facegen_var.get(),
            debug=self.debug_var.get(),
            log_file=self.log_file_var.get().strip() or None,
            game_data_dir=self.data_dir_var.get().strip() or None,
            output_dir=self.output_dir_var.get().strip() or None,
            profile_file=self.profile_file_var.get().strip() or None,
        )

    def _start_run(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        config = self._config_from_fields()
        load_order = self._build_load_order(config)

        self._clear_log()
        self._install_log_handler(config)
        self.run_button.configure(state="disabled", text="Running...")
        self.phase_var.set("Starting...")

        self._worker = threading.Thread(
            target=self._run_worker, args=(config, load_order), daemon=True)
        self._worker.start()
        self.after(50, self._drain_queue)

    def _build_load_order(
            self, config: FurrifierConfig) -> Optional[LoadOrder]:
        """Return an explicit LoadOrder if the user picked plugins, else None.

        None defers to run_furrification's default behaviour
        (LoadOrder.from_game(active_only=True)).
        """
        if self._plugin_override is None:
            return None
        data_dir = (Path(config.game_data_dir) if config.game_data_dir
                    else None)
        return LoadOrder.from_list(
            self._plugin_override, data_dir=data_dir, game_id="tes5")

    def _run_worker(self, config: FurrifierConfig,
                    load_order: Optional[LoadOrder]) -> None:
        try:
            run_furrification(config, load_order=load_order,
                              progress=self._on_progress)
            self._queue.put(("done", "0"))
        except Exception as exc:
            logging.getLogger(__name__).exception("Furrification failed: %s", exc)
            self._queue.put(("error", str(exc)))

    def _on_progress(self, phase: str) -> None:
        self._queue.put(("phase", phase))

    def _drain_queue(self) -> None:
        drained = False
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                drained = True
                if kind == "log":
                    self._append_log(payload)
                elif kind == "phase":
                    self.phase_var.set(payload)
                elif kind == "done":
                    self._on_worker_finished(success=True)
                elif kind == "error":
                    self._append_log(f"ERROR: {payload}")
                    self._on_worker_finished(success=False, error=payload)
        except queue.Empty:
            pass

        # Force a redraw so the user actually sees the new lines, instead of
        # them piling up until mainloop next hits idle.
        if drained:
            self.update_idletasks()

        if self._worker is not None and self._worker.is_alive():
            self.after(50, self._drain_queue)
        else:
            # One last drain after the worker exits, in case records slipped in
            # between the is_alive check and the poll above.
            self.after(50, self._final_drain)

    def _final_drain(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "phase":
                    self.phase_var.set(payload)
        except queue.Empty:
            pass

    def _on_worker_finished(self, *, success: bool, error: str = "") -> None:
        self.run_button.configure(state="normal", text="Run")
        self._remove_log_handler()
        if success:
            self.phase_var.set("Done.")
        else:
            self.phase_var.set("Failed.")
            messagebox.showerror("Furrifier", f"Furrification failed:\n{error}")

    # --- log plumbing ----------------------------------------------------

    def _install_log_handler(self, config: FurrifierConfig) -> None:
        root = logging.getLogger()
        level = logging.DEBUG if config.debug else logging.INFO
        root.setLevel(level)
        handler = _QueueLogHandler(self._queue)
        handler.setLevel(level)
        root.addHandler(handler)
        self._log_handler = handler

        # Log file is optional. The GUI used to silently ignore this
        # field — file logging only happened via setup_logging() on the
        # CLI path. Attach a FileHandler here so setting the field
        # actually writes something.
        self._file_handler: Optional[logging.FileHandler] = None
        if config.log_file:
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
                # Don't fail the run just because the log path was bad;
                # surface the problem via the queue handler instead.
                logging.getLogger(__name__).warning(
                    "could not open log file %r: %s", config.log_file, exc)

    def _remove_log_handler(self) -> None:
        root = logging.getLogger()
        if self._log_handler is not None:
            root.removeHandler(self._log_handler)
            self._log_handler = None
        if getattr(self, "_file_handler", None) is not None:
            root.removeHandler(self._file_handler)
            self._file_handler.close()
            self._file_handler = None

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


class PluginPickerDialog(ctk.CTkToplevel):
    """Modal checkbox list for picking which plugins to run against.

    Lists every *.esp/*.esm/*.esl file in the data dir. Plugins currently
    marked active in plugins.txt are pre-checked; inactive and
    not-yet-registered files are shown unchecked. OK returns the ordered
    list of checked plugin names via `result`; Cancel leaves `result = None`.
    """


    def __init__(self, parent: ctk.CTk, data_dir: Path,
                 initial_selection: Optional[list[str]] = None) -> None:
        super().__init__(parent)
        self.title("Select plugins")
        self.geometry("520x640")
        self.transient(parent)

        self.result: Optional[list[str]] = None
        # Each entry: {"name": str, "checked": bool, "iid": str (Treeview id)}
        self._items: list[dict] = []
        self._data_dir = data_dir
        # Cache of plugin-name.lower() -> list of master names (as declared
        # in the TES4 header). Populated lazily when a plugin is toggled on.
        self._master_cache: dict[str, list[str]] = {}
        # Fast lookup from plugin-name.lower() to item dict.
        self._by_name_lower: dict[str, dict] = {}

        plugins_in_order = self._collect_plugins(data_dir)
        if initial_selection is not None:
            checked = {p.lower() for p in initial_selection}
        else:
            checked = self._active_plugins()

        self._build_widgets(plugins_in_order, checked)

        # Modal grab has to happen after the window is visible, hence
        # after_idle rather than direct call.
        self.after(50, self._grab)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)


    def _grab(self) -> None:
        try:
            self.grab_set()
        except tk.TclError:
            pass


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
        return load_order_names + extras


    def _active_plugins(self) -> set[str]:
        try:
            lo = LoadOrder.from_game("tes5", active_only=True)
            return {name.lower() for name in lo.plugins}
        except Exception:
            return set()


    def _build_widgets(self, plugins: list[str], checked: set[str]) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # Top bar: filter entry + status line.
        top = ctk.CTkFrame(self)
        top.grid(row=0, column=0, padx=10, pady=(10, 6), sticky="ew")
        top.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top, text="Filter:").grid(
            row=0, column=0, padx=(10, 6), pady=(8, 4), sticky="w")
        self._filter_var = tk.StringVar(value="")
        self._filter_var.trace_add("write", lambda *_: self._apply_filter())
        ctk.CTkEntry(top, textvariable=self._filter_var,
                     placeholder_text="substring match, case-insensitive"
                     ).grid(
            row=0, column=1, padx=(0, 10), pady=(8, 4), sticky="ew")

        self._summary_var = tk.StringVar()
        ctk.CTkLabel(top, textvariable=self._summary_var, anchor="w").grid(
            row=1, column=0, columnspan=2, padx=10, pady=(0, 8), sticky="ew")
        ctk.CTkLabel(top, text="(right-click for bulk actions)",
                     anchor="e").grid(
            row=1, column=1, padx=(0, 10), pady=(0, 8), sticky="e")

        # Treeview + scrollbar. Single tree column holds "☑ name" or
        # "☐ name". Clicking a row toggles; right-click shows bulk-action
        # menu. Much lighter than a scrollable frame of CTkCheckBox rows.
        tree_frame = ctk.CTkFrame(self)
        tree_frame.grid(row=1, column=0, padx=10, pady=6, sticky="nsew")
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self._tree = ttk.Treeview(tree_frame, show="tree",
                                  selectmode="browse")
        self._tree.column("#0", width=460, stretch=True)
        self._tree.grid(row=0, column=0, padx=(6, 0), pady=6, sticky="nsew")

        scroll = ttk.Scrollbar(tree_frame, orient="vertical",
                               command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, padx=(0, 6), pady=6, sticky="ns")

        for name in plugins:
            is_checked = name.lower() in checked
            iid = self._tree.insert("", "end",
                                    text=self._format_row(is_checked, name))
            item = {"name": name, "checked": is_checked, "iid": iid}
            self._items.append(item)
            self._by_name_lower[name.lower()] = item

        # Bind interactions
        self._tree.bind("<Button-1>", self._on_row_click)
        self._tree.bind("<Button-3>", self._on_right_click)
        self._tree.bind("<space>", self._on_space)

        # Right-click menu. Underlines expose mnemonic keys: C / E / I.
        self._menu = tk.Menu(self, tearoff=0)
        self._menu.add_command(label="Check all", underline=0,
                               command=self._check_all)
        self._menu.add_command(label="Uncheck all", underline=4,
                               command=self._uncheck_all)
        self._menu.add_command(label="Invert selection", underline=0,
                               command=self._invert)

        # Bottom bar: Reset (left), Cancel/OK (right)
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=2, column=0, padx=10, pady=(6, 10), sticky="ew")
        bottom.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(bottom, text="Reset", width=100,
                      command=self._reset).grid(
            row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        ctk.CTkButton(bottom, text="Cancel", width=100,
                      command=self._on_cancel).grid(
            row=0, column=2, padx=(4, 8), pady=8)
        ctk.CTkButton(bottom, text="OK", width=100,
                      command=self._on_ok).grid(
            row=0, column=3, padx=(4, 8), pady=8)

        self._update_summary()


    @staticmethod
    def _format_row(checked: bool, name: str) -> str:
        return ("\u2611 " if checked else "\u2610 ") + name  # ☑ / ☐


    def _set_checked(self, item: dict, checked: bool) -> None:
        if item["checked"] == checked:
            return
        item["checked"] = checked
        self._tree.item(item["iid"],
                        text=self._format_row(checked, item["name"]))


    def _update_summary(self) -> None:
        total = len(self._items)
        checked = sum(1 for it in self._items if it["checked"])
        visible = len(self._tree.get_children(""))
        if visible == total:
            self._summary_var.set(f"{checked} / {total} checked")
        else:
            self._summary_var.set(
                f"{checked} / {total} checked ({visible} shown)")


    def _visible_items(self) -> list[dict]:
        """Items currently visible under the filter, in display order."""
        visible = set(self._tree.get_children(""))
        return [it for it in self._items if it["iid"] in visible]


    def _apply_filter(self) -> None:
        query = self._filter_var.get().strip().lower()
        # Detach everything, then reattach matches in original order so
        # the displayed list preserves load-order.
        for it in self._items:
            self._tree.detach(it["iid"])
        for it in self._items:
            if not query or query in it["name"].lower():
                self._tree.reattach(it["iid"], "", "end")
        self._update_summary()


    def _reset(self) -> None:
        """Restore the selection to the currently-active plugins."""
        active = self._active_plugins()
        for it in self._items:
            self._set_checked(it, it["name"].lower() in active)
        self._update_summary()


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
        """Check every transitive master of `name` that we know about.

        Unknown masters (declared in the plugin but not present in our
        list — e.g. the game's own implicit masters filtered out, or a
        missing dependency) are silently skipped.
        """
        seen: set[str] = set()
        queue = [name]
        while queue:
            current = queue.pop()
            for master in self._get_masters(current):
                key = master.lower()
                if key in seen:
                    continue
                seen.add(key)
                item = self._by_name_lower.get(key)
                if item is not None:
                    self._set_checked(item, True)
                queue.append(master)


    def _item_by_iid(self, iid: str) -> Optional[dict]:
        for it in self._items:
            if it["iid"] == iid:
                return it
        return None


    def _toggle(self, it: dict) -> None:
        """User-initiated toggle: flip state, pull in masters if enabling."""
        new_state = not it["checked"]
        self._set_checked(it, new_state)
        if new_state:
            self._pull_in_masters(it["name"])
        self._update_summary()


    def _on_row_click(self, event: tk.Event) -> None:
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        it = self._item_by_iid(iid)
        if it is not None:
            self._toggle(it)


    def _on_space(self, event: tk.Event) -> str:
        iid = self._tree.focus()
        if iid:
            it = self._item_by_iid(iid)
            if it is not None:
                self._toggle(it)
        return "break"


    def _on_right_click(self, event: tk.Event) -> None:
        try:
            self._menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._menu.grab_release()


    def _check_all(self) -> None:
        for it in self._visible_items():
            self._set_checked(it, True)
        self._update_summary()


    def _uncheck_all(self) -> None:
        for it in self._visible_items():
            self._set_checked(it, False)
        self._update_summary()


    def _invert(self) -> None:
        for it in self._visible_items():
            self._set_checked(it, not it["checked"])
        self._update_summary()


    def _on_ok(self) -> None:
        self.result = [it["name"] for it in self._items if it["checked"]]
        self._close()


    def _on_cancel(self) -> None:
        self.result = None
        self._close()


    def _close(self) -> None:
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def main() -> int:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("dark-blue")
    # Bolder accent: midnight-blue buttons, a tick brighter on hover.
    # Same color for light and dark mode so it reads the same either way.
    midnight = "#191970"
    midnight_hover = "#2f2fb5"
    midnight_dark = "#0b0b3a"
    theme = ctk.ThemeManager.theme
    theme["CTkButton"]["fg_color"] = [midnight, midnight]
    theme["CTkButton"]["hover_color"] = [midnight_hover, midnight_hover]
    theme["CTkButton"]["border_color"] = [midnight_dark, midnight_dark]
    theme["CTkCheckBox"]["fg_color"] = [midnight, midnight]
    theme["CTkCheckBox"]["hover_color"] = [midnight_hover, midnight_hover]
    theme["CTkCheckBox"]["border_color"] = [midnight_dark, midnight_dark]
    theme["CTkOptionMenu"]["fg_color"] = [midnight, midnight]
    theme["CTkOptionMenu"]["button_color"] = [midnight_dark, midnight_dark]
    theme["CTkOptionMenu"]["button_hover_color"] = [midnight_hover, midnight_hover]
    app = FurrifierWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
