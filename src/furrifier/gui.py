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
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk
from PIL import Image

from esplib import find_game_data

from .config import FurrifierConfig
from .main import run_furrification


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

        # Data dir — prefill with auto-detected path so the user sees
        # where the patch would go. They can still edit or browse.
        ctk.CTkLabel(form, text="Data dir:").grid(
            row=2, column=0, padx=(10, 8), pady=8, sticky="w")
        detected = find_game_data('tes5')
        self.data_dir_var = tk.StringVar(value=str(detected) if detected else "")
        ctk.CTkEntry(form, textvariable=self.data_dir_var,
                     placeholder_text="(not auto-detected)").grid(
            row=2, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Browse...", width=90,
                      command=self._browse_data_dir).grid(
            row=2, column=2, padx=(0, 10), pady=8)

        # Log file
        ctk.CTkLabel(form, text="Log file:").grid(
            row=3, column=0, padx=(10, 8), pady=8, sticky="w")
        self.log_file_var = tk.StringVar(value="")
        ctk.CTkEntry(form, textvariable=self.log_file_var,
                     placeholder_text="(optional)").grid(
            row=3, column=1, padx=(0, 8), pady=8, sticky="ew")
        ctk.CTkButton(form, text="Browse...", width=90,
                      command=self._browse_log_file).grid(
            row=3, column=2, padx=(0, 10), pady=8)

        # Option checkboxes
        opts = ctk.CTkFrame(self)
        opts.grid(row=2, column=0, padx=12, pady=6, sticky="ew")
        self.armor_var = tk.BooleanVar(value=True)
        self.male_var = tk.BooleanVar(value=True)
        self.female_var = tk.BooleanVar(value=True)
        self.schlongs_var = tk.BooleanVar(value=True)
        self.debug_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Furrify armor",
                        variable=self.armor_var).grid(row=0, column=0, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Male NPCs",
                        variable=self.male_var).grid(row=0, column=1, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Female NPCs",
                        variable=self.female_var).grid(row=0, column=2, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Schlongs (SOS)",
                        variable=self.schlongs_var).grid(row=0, column=3, padx=10, pady=8, sticky="w")
        ctk.CTkCheckBox(opts, text="Debug logging",
                        variable=self.debug_var).grid(row=0, column=4, padx=10, pady=8, sticky="w")

        # Log pane
        log_frame = ctk.CTkFrame(self)
        log_frame.grid(row=3, column=0, padx=12, pady=6, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        self.log_text = ctk.CTkTextbox(log_frame, wrap="word",
                                       font=("Consolas", 11))
        self.log_text.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")
        self.log_text.configure(state="disabled")

        # Bottom bar: phase label + progress + run button
        bottom = ctk.CTkFrame(self)
        bottom.grid(row=4, column=0, padx=12, pady=(6, 12), sticky="ew")
        bottom.grid_columnconfigure(0, weight=1)
        self.phase_var = tk.StringVar(value="Ready.")
        ctk.CTkLabel(bottom, textvariable=self.phase_var, anchor="w").grid(
            row=0, column=0, padx=10, pady=(8, 0), sticky="ew")
        self.progress = ctk.CTkProgressBar(bottom, mode="indeterminate")
        self.progress.grid(row=1, column=0, padx=10, pady=(4, 8), sticky="ew")
        self.progress.set(0)
        self.run_button = ctk.CTkButton(bottom, text="Run", width=120,
                                        command=self._start_run)
        self.run_button.grid(row=0, column=1, rowspan=2, padx=10, pady=8)

    # --- actions ---------------------------------------------------------

    def _browse_data_dir(self) -> None:
        path = filedialog.askdirectory(
            title="Select Skyrim Data directory",
            initialdir=self.data_dir_var.get() or None,
        )
        if path:
            self.data_dir_var.set(path)

    def _browse_log_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Log file",
            defaultextension=".log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if path:
            self.log_file_var.set(path)

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
            debug=self.debug_var.get(),
            log_file=self.log_file_var.get().strip() or None,
            game_data_dir=self.data_dir_var.get().strip() or None,
        )

    def _start_run(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return

        config = self._config_from_fields()

        self._clear_log()
        self._install_log_handler(config)
        self.run_button.configure(state="disabled", text="Running...")
        self.phase_var.set("Starting...")
        self.progress.start()

        self._worker = threading.Thread(
            target=self._run_worker, args=(config,), daemon=True)
        self._worker.start()
        self.after(50, self._drain_queue)

    def _run_worker(self, config: FurrifierConfig) -> None:
        try:
            run_furrification(config, progress=self._on_progress)
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
        self.progress.stop()
        self.progress.set(0)
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
        root.setLevel(logging.DEBUG if config.debug else logging.INFO)
        handler = _QueueLogHandler(self._queue)
        handler.setLevel(logging.DEBUG if config.debug else logging.INFO)
        root.addHandler(handler)
        self._log_handler = handler

    def _remove_log_handler(self) -> None:
        if self._log_handler is not None:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler = None

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> int:
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    app = FurrifierWindow()
    app.mainloop()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
