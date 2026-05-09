"""CADelta desktop GUI entry point.

Starts a TkinterDnD root window, loads persisted settings, and routes
between the main view and the settings page (same window, swapped via
``pack_forget``/``pack``). Run via ``python -m cadelta.gui.app`` or the
``cadelta-gui`` console script defined in ``pyproject.toml``.
"""
from __future__ import annotations

import sys
import tkinter as tk
from typing import Optional

from tkinterdnd2 import TkinterDnD

from .main_view import MainView
from .settings import SettingsState, load_settings, save_settings
from .settings_view import SettingsView


_WINDOW_TITLE = "CADelta"
_WINDOW_MIN_W = 560
_WINDOW_MIN_H = 360
_BG = "#f4f4f4"


class App:
    """Top-level application controller. Owns the root window and the
    current SettingsState; swaps the visible view between MainView and
    SettingsView in response to user actions.
    """

    def __init__(self) -> None:
        # TkinterDnD.Tk extends tk.Tk with drag-and-drop support — drop-in
        # replacement; everything else (geometry managers, widget classes)
        # works the same.
        self._root = TkinterDnD.Tk()
        self._root.title(_WINDOW_TITLE)
        self._root.minsize(_WINDOW_MIN_W, _WINDOW_MIN_H)
        self._root.configure(bg=_BG)

        # Sensible default size — 640×420 fits both views without scrolling
        # but can be resized larger.
        self._root.geometry("640x420")

        self._settings: SettingsState = load_settings()
        self._main_view: Optional[MainView] = None
        self._settings_view: Optional[SettingsView] = None

        self._show_main()

    # --- view routing ------------------------------------------------------

    def _show_main(self) -> None:
        if self._settings_view is not None:
            self._settings_view.pack_forget()
            self._settings_view.destroy()
            self._settings_view = None
        if self._main_view is None:
            self._main_view = MainView(
                self._root,
                settings=self._settings,
                on_open_settings=self._show_settings,
            )
        else:
            # Re-sync if settings were edited.
            self._main_view.refresh_after_settings(self._settings)
        self._main_view.pack(fill="both", expand=True)

    def _show_settings(self) -> None:
        if self._main_view is not None:
            self._main_view.pack_forget()
        self._settings_view = SettingsView(
            self._root,
            settings=self._settings,
            on_save=self._on_settings_saved,
            on_back=self._show_main,
        )
        self._settings_view.pack(fill="both", expand=True)

    def _on_settings_saved(self, new_state: SettingsState) -> None:
        self._settings = new_state
        save_settings(self._settings)
        self._show_main()

    # --- run loop ----------------------------------------------------------

    def run(self) -> None:
        self._root.mainloop()


def main() -> int:
    """Console-script entry point. Returns an exit code so PyInstaller
    sees a clean exit."""
    try:
        App().run()
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
