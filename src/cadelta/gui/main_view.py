"""Main page: two file drop zones, a Compare button, progress bar, status
line, and a Settings button.

The diff runs on a :class:`QThread` via :class:`cadelta.gui.qt_worker.DiffWorker`
so the window stays responsive. All widget updates happen on the main thread
through queued signal connections.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .qt_worker import DiffWorker
from .settings import SettingsState
from .worker import DiffJob, DoneMessage


_VALID_EXTS = {".step", ".stp"}
_FILE_FILTER = "STEP files (*.step *.stp);;All files (*)"


def _looks_like_step(path: str) -> bool:
    return Path(path).suffix.lower() in _VALID_EXTS


def open_folder(path: Path) -> None:
    """Reveal the output file's folder in the OS file explorer. Best effort:
    a failure here must never crash the GUI."""
    folder = path.parent
    try:
        if sys.platform == "win32":
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(folder)], check=False)
        else:
            subprocess.run(["xdg-open", str(folder)], check=False)
    except Exception:
        pass


class DropZone(QFrame):
    """Drop target + click target for one STEP file. Emits :attr:`chosen`
    with the resolved path whenever the user drops or browses to one."""

    chosen = Signal(Path)

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(150)

        self._prompt = prompt
        self._path: Optional[Path] = None

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        self._label = QLabel(prompt)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setProperty("role", "muted")
        layout.addWidget(self._label)

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def reset(self) -> None:
        self._path = None
        self._label.setText(self._prompt)
        self._label.setProperty("role", "muted")
        self._set_state(active=False, filled=False)

    # --- mouse / drag-drop ------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        if event.button() != Qt.LeftButton:
            return
        path_str, _ = QFileDialog.getOpenFileName(
            self, f"Select {self._prompt}", "", _FILE_FILTER
        )
        if not path_str:
            return
        if not _looks_like_step(path_str):
            QMessageBox.warning(self, "Wrong file type", "Please pick a .step or .stp file.")
            return
        self._accept(Path(path_str))

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._first_step_url(event) is not None:
            event.acceptProposedAction()
            self._set_state(active=True, filled=self._path is not None)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_state(active=False, filled=self._path is not None)

    def dropEvent(self, event) -> None:  # noqa: N802
        path = self._first_step_url(event)
        self._set_state(active=False, filled=self._path is not None)
        if path is None:
            QMessageBox.warning(self, "Wrong file type", "Please drop a .step or .stp file.")
            return
        event.acceptProposedAction()
        self._accept(path)

    @staticmethod
    def _first_step_url(event) -> Optional[Path]:
        mime = event.mimeData()
        if not mime.hasUrls():
            return None
        for url in mime.urls():
            local = url.toLocalFile()
            if local and _looks_like_step(local):
                return Path(local)
        return None

    # --- helpers ----------------------------------------------------------

    def _accept(self, path: Path) -> None:
        self._path = path
        self._label.setText(path.name)
        self._label.setProperty("role", None)
        self._set_state(active=False, filled=True)
        self.chosen.emit(path)

    def _set_state(self, *, active: bool, filled: bool) -> None:
        # Dynamic properties drive the QSS border/background; repolish so the
        # style engine re-evaluates the selectors after the change.
        self.setProperty("active", "true" if active else "false")
        self.setProperty("filled", "true" if filled else "false")
        for w in (self, self._label):
            w.style().unpolish(w)
            w.style().polish(w)


class MainView(QWidget):
    """Default screen. Holds the live :class:`SettingsState` so the next
    Compare uses whatever the user last saved on the settings page."""

    def __init__(
        self,
        settings: SettingsState,
        on_open_settings: Callable[[], None],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._on_open_settings = on_open_settings
        self._thread: Optional[QThread] = None
        self._worker: Optional[DiffWorker] = None
        self._last_output: Optional[Path] = None
        self._build()

    # --- public -----------------------------------------------------------

    def refresh_after_settings(self, settings: SettingsState) -> None:
        """Adopt edited settings when the user returns from the settings page."""
        self._settings = settings

    # --- layout -----------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 18)
        outer.setSpacing(14)

        # Top bar: title + Settings.
        topbar = QHBoxLayout()
        title = QLabel("CADelta")
        title.setProperty("role", "title")
        topbar.addWidget(title)
        subtitle = QLabel("Visual STEP diff")
        subtitle.setProperty("role", "muted")
        topbar.addWidget(subtitle)
        topbar.addStretch(1)
        settings_btn = QPushButton("Settings")
        settings_btn.setObjectName("ghost")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.clicked.connect(self._on_open_settings)
        topbar.addWidget(settings_btn)
        outer.addLayout(topbar)

        # Drop zones row.
        zones = QHBoxLayout()
        zones.setSpacing(12)
        self._zone_v1 = DropZone("Drop v1.step\nor click to browse")
        self._zone_v2 = DropZone("Drop v2.step\nor click to browse")
        self._zone_v1.chosen.connect(lambda _p: self._update_compare_state())
        self._zone_v2.chosen.connect(lambda _p: self._update_compare_state())
        zones.addWidget(self._zone_v1)
        zones.addWidget(self._zone_v2)
        outer.addLayout(zones, stretch=1)

        # Compare button.
        self._compare_btn = QPushButton("Compare")
        self._compare_btn.setObjectName("primary")
        self._compare_btn.setEnabled(False)
        self._compare_btn.setCursor(Qt.PointingHandCursor)
        self._compare_btn.clicked.connect(self._on_compare)
        outer.addWidget(self._compare_btn)

        # Progress + status.
        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 1)  # idle: a quiet, empty bar
        self._progress.setValue(0)
        outer.addWidget(self._progress)

        self._status = QLabel("")
        self._status.setProperty("role", "muted")
        self._status.setWordWrap(True)
        self._status.setCursor(Qt.ArrowCursor)
        self._status.mousePressEvent = self._on_status_clicked  # type: ignore[assignment]
        outer.addWidget(self._status)

    # --- events -----------------------------------------------------------

    def _busy(self) -> bool:
        return self._thread is not None

    def _update_compare_state(self) -> None:
        ready = self._zone_v1.path is not None and self._zone_v2.path is not None
        self._compare_btn.setEnabled(ready and not self._busy())

    def _on_compare(self) -> None:
        v1, v2 = self._zone_v1.path, self._zone_v2.path
        if v1 is None or v2 is None or self._busy():
            return

        suggested = str(v2.parent / f"{v2.stem}_diff.step")
        out_str, _ = QFileDialog.getSaveFileName(
            self, "Save diff as", suggested, "STEP files (*.step);;All files (*)"
        )
        if not out_str:
            return  # cancelled
        out_step = Path(out_str)
        if out_step.suffix == "":
            out_step = out_step.with_suffix(".step")

        # Disable Compare while running so users can't race two diffs on the
        # writer's module-level color state.
        self._compare_btn.setEnabled(False)
        # Drop the previous run's output link so the status label can't be
        # clicked to open the stale folder while this new diff is running.
        self._last_output = None
        self._status.setCursor(Qt.ArrowCursor)
        self._set_status("Starting...", role="muted")
        self._progress.setRange(0, 0)  # busy/indeterminate animation

        job = DiffJob(v1_path=v1, v2_path=v2, out_step=out_step, settings=self._settings)
        self._start_worker(job)

    def _start_worker(self, job: DiffJob) -> None:
        self._thread = QThread(self)
        self._worker = DiffWorker(job)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.phase.connect(self._on_phase)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._thread.quit)
        # Clean teardown once the event loop has fully unwound.
        self._thread.finished.connect(self._on_thread_finished)
        self._thread.start()

    def _on_phase(self, text: str) -> None:
        self._set_status(text, role="muted")

    def _on_succeeded(self, msg: DoneMessage) -> None:
        self._last_output = msg.out_step
        self._set_status(
            f"Wrote {msg.out_step.name}  -  click to open folder", role="link"
        )
        self._status.setCursor(Qt.PointingHandCursor)

    def _on_failed(self, message: str) -> None:
        self._last_output = None
        self._set_status("", role="muted")
        self._status.setCursor(Qt.ArrowCursor)
        QMessageBox.critical(self, "Diff failed", message)

    def _on_thread_finished(self) -> None:
        # Stop the indeterminate animation and release the thread/worker.
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._update_compare_state()

    def _on_status_clicked(self, _event) -> None:
        if self._last_output is not None:
            open_folder(self._last_output)

    def _set_status(self, text: str, *, role: str) -> None:
        self._status.setText(text)
        self._status.setProperty("role", role)
        self._status.style().unpolish(self._status)
        self._status.style().polish(self._status)
