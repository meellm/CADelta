"""Settings page: per-status checkbox + color picker rows, output toggles,
tolerance fields, and a Save button that swaps back to the main view.

Tick semantics (per user spec, mirrored in :mod:`cadelta.gui.worker`):

- MOVED / ADDED unticked → use the writer's default color, still rendered.
- REMOVED / MOVED_FROM unticked → omit those bodies from the output.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, ttk
from typing import Callable, Tuple

from .settings import RGB, SettingsState, StatusSetting


_BG = "#f4f4f4"
_TEXT = "#222222"
_TEXT_MUTED = "#666666"
_DIVIDER = "#dcdcdc"


def _rgb_to_hex(rgb: RGB) -> str:
    """Convert (0..1, 0..1, 0..1) → ``#rrggbb`` for Tk widgets."""
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(hex_str: str) -> RGB:
    h = hex_str.lstrip("#")
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


class _StatusRow(tk.Frame):
    """One row in the colors section: [✓ tick] [LABEL] [color swatch] [Pick…]."""

    def __init__(self, parent: tk.Widget, label: str, setting: StatusSetting):
        super().__init__(parent, bg=_BG)
        self._setting = setting

        # Tk's ``BooleanVar`` is the cleanest way to bind a checkbutton.
        self._enabled_var = tk.BooleanVar(value=setting.enabled)
        chk = ttk.Checkbutton(self, variable=self._enabled_var)
        chk.grid(row=0, column=0, padx=(0, 8))

        lbl = tk.Label(self, text=label, bg=_BG, fg=_TEXT, width=14, anchor="w")
        lbl.grid(row=0, column=1, sticky="w")

        # Swatch is a tk.Frame with the color as bg — small, framed.
        self._swatch = tk.Frame(
            self, width=28, height=18,
            bg=_rgb_to_hex(setting.color),
            highlightthickness=1, highlightbackground="#999999",
        )
        # Stop the frame collapsing to its content's natural size.
        self._swatch.grid_propagate(False)
        self._swatch.grid(row=0, column=2, padx=(0, 8))

        pick = ttk.Button(self, text="Pick…", width=8, command=self._pick_color)
        pick.grid(row=0, column=3)

        self.columnconfigure(1, weight=1)

    def _pick_color(self) -> None:
        # Tk's color chooser returns ((r, g, b), "#rrggbb") in 0..255 ints.
        # The hex string is the most reliable form to round-trip.
        result = colorchooser.askcolor(
            initialcolor=_rgb_to_hex(self._setting.color),
            title="Pick a color",
        )
        if result is None or result[1] is None:
            return  # user cancelled
        new_rgb = _hex_to_rgb(result[1])
        self._setting = StatusSetting(enabled=self._enabled_var.get(), color=new_rgb)
        self._swatch.configure(bg=result[1])

    def current(self) -> StatusSetting:
        """Return the row's live state, including the latest tick value."""
        return StatusSetting(enabled=self._enabled_var.get(), color=self._setting.color)


class SettingsView(tk.Frame):
    """The whole settings page. Built on demand each time the gear icon is
    clicked — cheap (no heavy state) and keeps widget references local.

    Calls ``on_save`` with the new SettingsState when the user clicks Save,
    ``on_back`` when they click ← Back without saving.
    """

    def __init__(
        self,
        parent: tk.Widget,
        settings: SettingsState,
        on_save: Callable[[SettingsState], None],
        on_back: Callable[[], None],
    ):
        super().__init__(parent, bg=_BG)
        self._settings = settings
        self._on_save = on_save
        self._on_back = on_back
        self._build()

    def _build(self) -> None:
        # Header bar with Back button (left) and section title (centered).
        header = tk.Frame(self, bg=_BG)
        header.pack(fill="x", padx=12, pady=(8, 0))
        back = ttk.Button(header, text="← Back", command=self._on_back)
        back.pack(side="left")
        title = tk.Label(header, text="Settings", bg=_BG, fg=_TEXT,
                         font=("TkDefaultFont", 12, "bold"))
        title.pack(side="left", padx=12)

        body = tk.Frame(self, bg=_BG)
        body.pack(fill="both", expand=True, padx=20, pady=12)

        # --- Colors section -----------------------------------------------
        tk.Label(
            body, text="Diff colors", bg=_BG, fg=_TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")

        self._row_moved = _StatusRow(body, "MOVED", self._settings.moved)
        self._row_added = _StatusRow(body, "ADDED", self._settings.added)
        self._row_removed = _StatusRow(body, "REMOVED", self._settings.removed)
        self._row_moved_from = _StatusRow(body, "MOVED_FROM", self._settings.moved_from)
        for r in (self._row_moved, self._row_added, self._row_removed, self._row_moved_from):
            r.pack(fill="x", pady=2)

        note = tk.Label(
            body,
            text=(
                "• Unticking REMOVED or MOVED_FROM omits those bodies from the output.\n"
                "• Unticking MOVED or ADDED keeps them in the output but uses the\n"
                "  default color instead of your picked one."
            ),
            bg=_BG, fg=_TEXT_MUTED, justify="left",
            font=("TkDefaultFont", 9),
        )
        note.pack(anchor="w", pady=(4, 0))

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # --- Outputs section ----------------------------------------------
        tk.Label(
            body, text="Extra outputs", bg=_BG, fg=_TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")
        self._json_var = tk.BooleanVar(value=self._settings.write_json_report)
        self._xlsx_var = tk.BooleanVar(value=self._settings.write_excel_report)
        ttk.Checkbutton(
            body, variable=self._json_var,
            text="Write JSON report (alongside diff.step)",
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            body, variable=self._xlsx_var,
            text="Write Excel report (alongside diff.step)",
        ).pack(anchor="w", pady=2)

        ttk.Separator(body, orient="horizontal").pack(fill="x", pady=12)

        # --- Tolerances ---------------------------------------------------
        tk.Label(
            body, text="Tolerances", bg=_BG, fg=_TEXT,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w")

        tol_row = tk.Frame(body, bg=_BG)
        tol_row.pack(fill="x", pady=4)
        tk.Label(tol_row, text="Translation (mm):", bg=_BG, fg=_TEXT,
                 width=20, anchor="w").grid(row=0, column=0, sticky="w")
        self._tol_mm_var = tk.StringVar(value=f"{self._settings.tol_mm:g}")
        ttk.Entry(tol_row, textvariable=self._tol_mm_var, width=10).grid(row=0, column=1)
        tk.Label(tol_row, text="Rotation (deg):", bg=_BG, fg=_TEXT,
                 width=20, anchor="w").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._tol_deg_var = tk.StringVar(value=f"{self._settings.tol_deg:g}")
        ttk.Entry(tol_row, textvariable=self._tol_deg_var, width=10).grid(row=1, column=1, pady=(4, 0))

        # --- Save button --------------------------------------------------
        save_row = tk.Frame(self, bg=_BG)
        save_row.pack(fill="x", pady=(0, 12))
        ttk.Button(save_row, text="Save", command=self._save).pack()

    def _parse_float(self, var: tk.StringVar, fallback: float) -> float:
        """Tolerant float parse — empty / malformed input keeps the fallback
        so the user doesn't get an error popup mid-edit."""
        try:
            return float(var.get())
        except (ValueError, TypeError):
            return fallback

    def _save(self) -> None:
        new_state = SettingsState(
            moved=self._row_moved.current(),
            added=self._row_added.current(),
            removed=self._row_removed.current(),
            moved_from=self._row_moved_from.current(),
            write_json_report=self._json_var.get(),
            write_excel_report=self._xlsx_var.get(),
            tol_mm=self._parse_float(self._tol_mm_var, self._settings.tol_mm),
            tol_deg=self._parse_float(self._tol_deg_var, self._settings.tol_deg),
        )
        self._on_save(new_state)
