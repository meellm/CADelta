"""CADelta desktop GUI entry point.

Builds a PySide6 QApplication, applies the dark theme, and shows a QMainWindow
that swaps between the main view and the settings page via a QStackedWidget.
Run via ``python -m cadelta.gui.app`` or the ``cadelta-gui`` console script
defined in ``pyproject.toml``.
"""
from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtWidgets import QApplication, QMainWindow, QStackedWidget

from .main_view import MainView
from .settings import SettingsState, load_settings, save_settings
from .settings_view import SettingsView
from .theme import apply_theme


_WINDOW_TITLE = "CADelta"
_WINDOW_W = 720
_WINDOW_H = 540
_WINDOW_MIN_W = 560
_WINDOW_MIN_H = 460


class MainWindow(QMainWindow):
    """Top-level window. Owns the current :class:`SettingsState` and routes
    between MainView and SettingsView in a single stacked widget."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(_WINDOW_TITLE)
        self.resize(_WINDOW_W, _WINDOW_H)
        self.setMinimumSize(_WINDOW_MIN_W, _WINDOW_MIN_H)

        self._settings: SettingsState = load_settings()

        self._stack = QStackedWidget()
        self._stack.setObjectName("root")
        self.setCentralWidget(self._stack)

        self._main_view = MainView(self._settings, on_open_settings=self._show_settings)
        self._stack.addWidget(self._main_view)

        self._settings_view: Optional[SettingsView] = None
        self._stack.setCurrentWidget(self._main_view)

    # --- routing ----------------------------------------------------------

    def _show_settings(self) -> None:
        # Rebuilt each time so the form reflects the live settings state and we
        # don't carry stale widget references after a save.
        if self._settings_view is not None:
            self._stack.removeWidget(self._settings_view)
            self._settings_view.deleteLater()
        self._settings_view = SettingsView(
            self._settings, on_save=self._on_settings_saved, on_back=self._show_main
        )
        self._stack.addWidget(self._settings_view)
        self._stack.setCurrentWidget(self._settings_view)

    def _show_main(self) -> None:
        self._main_view.refresh_after_settings(self._settings)
        self._stack.setCurrentWidget(self._main_view)

    def _on_settings_saved(self, new_state: SettingsState) -> None:
        self._settings = new_state
        save_settings(self._settings)
        self._show_main()


def build_app(argv: Optional[list[str]] = None) -> QApplication:
    """Return a themed QApplication, reusing an existing instance if one is
    already running (lets tests construct widgets without a second app)."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    apply_theme(app)
    return app


def main() -> int:
    """Console-script entry point. Returns an exit code so PyInstaller sees a
    clean exit."""
    app = build_app()
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
