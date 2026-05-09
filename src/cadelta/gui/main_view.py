"""Main view: two file drop zones, Compare button, progress bar, status line,
and a settings (⚙) icon that swaps in :mod:`cadelta.gui.settings_view`.

All Tk operations stay on the main thread; the diff itself runs in a worker
thread (see :mod:`cadelta.gui.worker`). The view polls a ``queue.Queue`` via
``after()`` to update widgets when the worker reports phase changes.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from tkinterdnd2 import DND_FILES

from .settings import SettingsState, save_settings
from .worker import (
    DiffJob,
    DoneMessage,
    ErrorMessage,
    PhaseMessage,
    start_worker,
)


# UI palette. Kept small + greyscale to stay neutral against any STEP file
# diff colors the user picks; we don't want the GUI itself competing with
# the cyan/yellow/red of the actual diff renderings.
_BG = "#f4f4f4"
_DROP_BG = "#eaeaea"
_DROP_BG_ACTIVE = "#d6e9f5"   # subtle blue while a drop hovers
_DROP_BORDER = "#bdbdbd"
_TEXT = "#222222"
_TEXT_MUTED = "#666666"


_VALID_EXTS = {".step", ".stp"}


def _looks_like_step(path: str) -> bool:
    return Path(path).suffix.lower() in _VALID_EXTS


class DropZone(tk.Frame):
    """A drop target + click target for one STEP file. Calls ``on_chosen``
    with the resolved path whenever the user drops or browses to one.
    """

    def __init__(self, parent: tk.Widget, label: str, on_chosen: Callable[[Path], None]):
        # `tk.Frame` rather than `ttk.Frame` because we want a colored
        # border + bg and ttk's themed engine on Windows ignores those.
        super().__init__(
            parent,
            bg=_DROP_BG,
            highlightthickness=2,
            highlightbackground=_DROP_BORDER,
            highlightcolor=_DROP_BORDER,
        )
        self._label_text = label
        self._on_chosen = on_chosen
        self._path: Optional[Path] = None

        # The label fills the frame so the entire visible square is a click
        # target (and a drop target — we register both on the frame).
        self._label = tk.Label(
            self, text=label, bg=_DROP_BG, fg=_TEXT_MUTED,
            font=("TkDefaultFont", 11),
            justify="center",
        )
        self._label.pack(expand=True, fill="both", padx=12, pady=12)

        # Bindings — frame and label both, so a click anywhere works.
        for w in (self, self._label):
            w.bind("<Button-1>", self._on_click)

        # Drag-and-drop registration (tkinterdnd2 extension).
        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<DropEnter>>", lambda e: self._set_active(True))
        self.dnd_bind("<<DropLeave>>", lambda e: self._set_active(False))
        self.dnd_bind("<<Drop>>", self._on_drop)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def reset(self) -> None:
        self._path = None
        self._label.configure(text=self._label_text, fg=_TEXT_MUTED)

    def _set_active(self, active: bool) -> None:
        bg = _DROP_BG_ACTIVE if active else _DROP_BG
        self.configure(bg=bg)
        self._label.configure(bg=bg)

    def _on_click(self, _event=None) -> None:
        path_str = filedialog.askopenfilename(
            title=f"Select {self._label_text}",
            filetypes=[("STEP files", "*.step *.stp"), ("All files", "*.*")],
        )
        if not path_str:
            return
        if not _looks_like_step(path_str):
            messagebox.showerror("Wrong file type", "Please pick a .step or .stp file.")
            return
        self._accept(Path(path_str))

    def _on_drop(self, event) -> None:
        self._set_active(False)
        # ``event.data`` is a Tcl list — splitlist handles braces and spaces.
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        if not paths:
            return
        chosen = paths[0]
        if not _looks_like_step(chosen):
            messagebox.showerror("Wrong file type", "Please drop a .step or .stp file.")
            return
        self._accept(Path(chosen))

    def _accept(self, path: Path) -> None:
        self._path = path
        # Show just the filename — the full path is too long for the visual.
        self._label.configure(text=path.name, fg=_TEXT)
        self._on_chosen(path)


class MainView(tk.Frame):
    """The default screen: drop zones, Compare button, progress, status,
    and the ⚙ settings icon up top. Holds a reference to the running
    settings state so it stays in sync when the user edits and saves.
    """

    def __init__(
        self,
        parent: tk.Widget,
        settings: SettingsState,
        on_open_settings: Callable[[], None],
    ):
        super().__init__(parent, bg=_BG)
        self._settings = settings
        self._on_open_settings = on_open_settings
        self._queue: "queue.Queue" = queue.Queue()
        self._worker_running = False
        self._poll_id: Optional[str] = None
        self._build()

    # --- public ------------------------------------------------------------

    def refresh_after_settings(self, settings: SettingsState) -> None:
        """Called by the app when the user closes the settings page —
        capture the new settings so the next Compare uses them."""
        self._settings = settings
        # Persist on every settings-page close. Cheap and keeps the on-disk
        # state in sync with what the GUI shows.
        save_settings(self._settings)

    # --- layout ------------------------------------------------------------

    def _build(self) -> None:
        # Top bar with the gear icon. We use the unicode ⚙ glyph — keeps the
        # exe free of bundled image assets.
        topbar = tk.Frame(self, bg=_BG)
        topbar.pack(fill="x", padx=10, pady=(8, 0))
        gear = tk.Button(
            topbar, text="⚙", relief="flat", bg=_BG, fg=_TEXT,
            activebackground=_BG, font=("TkDefaultFont", 14),
            command=self._on_open_settings, cursor="hand2",
        )
        gear.pack(side="right")

        # Drop zones row.
        zones = tk.Frame(self, bg=_BG)
        zones.pack(fill="both", expand=True, padx=20, pady=10)
        zones.columnconfigure(0, weight=1, uniform="dz")
        zones.columnconfigure(1, weight=1, uniform="dz")
        zones.rowconfigure(0, weight=1)

        self._zone_v1 = DropZone(zones, "Drop v1.step\nor click to browse", self._on_v1_chosen)
        self._zone_v2 = DropZone(zones, "Drop v2.step\nor click to browse", self._on_v2_chosen)
        self._zone_v1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._zone_v2.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        # Compare button.
        btn_row = tk.Frame(self, bg=_BG)
        btn_row.pack(pady=(4, 8))
        self._compare_btn = ttk.Button(
            btn_row, text="Compare", command=self._on_compare, state="disabled",
        )
        self._compare_btn.pack()

        # Progress + status. Hidden when idle by setting the bar's mode to
        # determinate at 0; the bar itself stays in the layout so the panel
        # height is stable.
        progress_row = tk.Frame(self, bg=_BG)
        progress_row.pack(fill="x", padx=20, pady=(0, 12))
        self._progress = ttk.Progressbar(progress_row, mode="indeterminate")
        self._progress.pack(fill="x")
        self._status = tk.Label(
            progress_row, text="", bg=_BG, fg=_TEXT_MUTED,
            anchor="w", justify="left",
        )
        self._status.pack(fill="x", pady=(4, 0))

    # --- events ------------------------------------------------------------

    def _on_v1_chosen(self, _path: Path) -> None:
        self._update_compare_state()

    def _on_v2_chosen(self, _path: Path) -> None:
        self._update_compare_state()

    def _update_compare_state(self) -> None:
        ready = self._zone_v1.path is not None and self._zone_v2.path is not None
        self._compare_btn.configure(state=("normal" if ready and not self._worker_running else "disabled"))

    def _on_compare(self) -> None:
        v1 = self._zone_v1.path
        v2 = self._zone_v2.path
        if v1 is None or v2 is None:
            return  # Compare was somehow clicked while disabled — defensive.

        # One save dialog. STEP path is what the user picks; JSON / Excel
        # outputs (if enabled in settings) are derived from this stem.
        suggested = f"{v2.stem}_diff.step"
        out_str = filedialog.asksaveasfilename(
            title="Save diff as",
            defaultextension=".step",
            initialdir=str(v2.parent),
            initialfile=suggested,
            filetypes=[("STEP files", "*.step"), ("All files", "*.*")],
        )
        if not out_str:
            return  # user cancelled
        out_step = Path(out_str)

        # Disable Compare while the worker runs so the user can't queue
        # multiple diffs that would race on the writer's module-level state.
        self._worker_running = True
        self._compare_btn.configure(state="disabled")
        self._progress.configure(mode="indeterminate")
        self._progress.start(10)
        self._status.configure(text="Starting…", fg=_TEXT_MUTED)

        job = DiffJob(v1_path=v1, v2_path=v2, out_step=out_step, settings=self._settings)
        start_worker(job, self._queue)
        # Kick off the queue poller. 100 ms is enough for a smooth UI while
        # not burning CPU on an idle window.
        self._poll_id = self.after(100, self._drain_queue)

    def _drain_queue(self) -> None:
        try:
            while True:
                msg = self._queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass

        if self._worker_running:
            self._poll_id = self.after(100, self._drain_queue)
        else:
            self._poll_id = None

    def _handle_message(self, msg) -> None:
        if isinstance(msg, PhaseMessage):
            self._status.configure(text=msg.text, fg=_TEXT_MUTED)
        elif isinstance(msg, DoneMessage):
            self._on_done(msg)
        elif isinstance(msg, ErrorMessage):
            self._on_error(msg)

    def _on_done(self, msg: DoneMessage) -> None:
        self._worker_running = False
        self._progress.stop()
        # Build a one-line summary that doubles as a "click to open folder"
        # affordance. Underline + cursor change make the click target clear.
        out_path = msg.out_step
        text = f"Wrote {out_path.name}  (click to open folder)"
        self._status.configure(
            text=text, fg="#1a73e8", cursor="hand2",
            font=("TkDefaultFont", 10, "underline"),
        )
        self._status.bind("<Button-1>", lambda _e, p=out_path: self._open_folder(p))
        self._update_compare_state()

    def _on_error(self, msg: ErrorMessage) -> None:
        self._worker_running = False
        self._progress.stop()
        self._status.configure(
            text="", fg=_TEXT_MUTED,
            font=("TkDefaultFont", 10),
            cursor="",
        )
        self._status.unbind("<Button-1>")
        messagebox.showerror("Diff failed", msg.message)
        self._update_compare_state()

    @staticmethod
    def _open_folder(path: Path) -> None:
        """Reveal the output folder in the OS file explorer.

        Cross-platform branches because each OS has a different magic call:
        - Windows: ``os.startfile`` on the directory.
        - macOS:   ``open`` on the directory.
        - Linux:   ``xdg-open`` (best effort; no-op on minimal containers).
        """
        folder = path.parent
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder)], check=False)
        except Exception:
            # Any failure here shouldn't crash the GUI — the file is still
            # at the path the user picked, they can navigate manually.
            pass
