"""Settings page: per-status enable + color rows, output toggles, tolerance
fields, and Save / Back actions.

Tick semantics (mirrored in :mod:`cadelta.gui.worker`):

- MOVED / ADDED unticked: use the writer's default color, still rendered.
- REMOVED / MOVED_FROM unticked: omit those bodies from the output.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .settings import RGB, SettingsState, StatusSetting
from .theme import color as token


def _rgb_to_qcolor(rgb: RGB) -> QColor:
    r, g, b = (max(0, min(255, round(c * 255))) for c in rgb)
    return QColor(r, g, b)


def _qcolor_to_rgb(c: QColor) -> RGB:
    return (c.red() / 255.0, c.green() / 255.0, c.blue() / 255.0)


class _StatusRow(QWidget):
    """One row: [enable] [LABEL] ... [color swatch] [Pick...]."""

    def __init__(self, label: str, setting: StatusSetting) -> None:
        super().__init__()
        self._color: RGB = setting.color

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        self._check = QCheckBox()
        self._check.setChecked(setting.enabled)
        row.addWidget(self._check)

        name = QLabel(label)
        name.setMinimumWidth(120)
        row.addWidget(name)
        row.addStretch(1)

        self._swatch = QPushButton()
        self._swatch.setObjectName("swatch")
        self._swatch.setCursor(Qt.PointingHandCursor)
        self._swatch.clicked.connect(self._pick_color)
        self._apply_swatch()
        row.addWidget(self._swatch)

        pick = QPushButton("Pick...")
        pick.setCursor(Qt.PointingHandCursor)
        pick.clicked.connect(self._pick_color)
        row.addWidget(pick)

    def _apply_swatch(self) -> None:
        c = _rgb_to_qcolor(self._color)
        # Per-widget background; the #swatch QSS rule supplies border + size.
        self._swatch.setStyleSheet(
            f"QPushButton#swatch {{ background: {c.name()};"
            f" border: 1px solid {token('border_strong')}; }}"
        )

    def _pick_color(self) -> None:
        picked = QColorDialog.getColor(
            _rgb_to_qcolor(self._color), self, "Pick a color"
        )
        if not picked.isValid():
            return  # cancelled
        self._color = _qcolor_to_rgb(picked)
        self._apply_swatch()

    def current(self) -> StatusSetting:
        return StatusSetting(enabled=self._check.isChecked(), color=self._color)


class SettingsView(QWidget):
    """Whole settings page. Calls ``on_save`` with a fresh SettingsState on
    Save, ``on_back`` on Back (discarding edits)."""

    def __init__(
        self,
        settings: SettingsState,
        on_save: Callable[[SettingsState], None],
        on_back: Callable[[], None],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._on_save = on_save
        self._on_back = on_back
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 18)
        outer.setSpacing(14)

        # Header: back + title.
        header = QHBoxLayout()
        back = QPushButton("Back")
        back.setObjectName("ghost")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self._on_back)
        header.addWidget(back)
        title = QLabel("Settings")
        title.setProperty("role", "title")
        header.addWidget(title)
        header.addStretch(1)
        outer.addLayout(header)

        # --- Diff colors ---------------------------------------------------
        outer.addWidget(self._section_label("Diff colors"))
        colors_card = self._card()
        cv = QVBoxLayout(colors_card)
        cv.setSpacing(8)
        self._row_moved = _StatusRow("MOVED", self._settings.moved)
        self._row_added = _StatusRow("ADDED", self._settings.added)
        self._row_removed = _StatusRow("REMOVED", self._settings.removed)
        self._row_moved_from = _StatusRow("MOVED_FROM", self._settings.moved_from)
        for r in (self._row_moved, self._row_added, self._row_removed, self._row_moved_from):
            cv.addWidget(r)
        outer.addWidget(colors_card)

        note = QLabel(
            "Unticking REMOVED or MOVED_FROM omits those bodies from the output.\n"
            "Unticking MOVED or ADDED keeps them but uses the default color."
        )
        note.setProperty("role", "hint")
        note.setWordWrap(True)
        outer.addWidget(note)

        # --- Extra outputs -------------------------------------------------
        outer.addWidget(self._section_label("Extra outputs"))
        outputs_card = self._card()
        ov = QVBoxLayout(outputs_card)
        ov.setSpacing(8)
        self._json_check = QCheckBox("Write JSON report (alongside diff.step)")
        self._json_check.setChecked(self._settings.write_json_report)
        self._xlsx_check = QCheckBox("Write Excel report (alongside diff.step)")
        self._xlsx_check.setChecked(self._settings.write_excel_report)
        ov.addWidget(self._json_check)
        ov.addWidget(self._xlsx_check)
        outer.addWidget(outputs_card)

        # --- Tolerances ----------------------------------------------------
        outer.addWidget(self._section_label("Tolerances"))
        tol_card = self._card()
        tg = QGridLayout(tol_card)
        tg.setHorizontalSpacing(12)
        tg.setVerticalSpacing(8)
        tg.addWidget(QLabel("Translation (mm)"), 0, 0)
        self._tol_mm = self._tol_spin(self._settings.tol_mm)
        tg.addWidget(self._tol_mm, 0, 1)
        tg.addWidget(QLabel("Rotation (deg)"), 1, 0)
        self._tol_deg = self._tol_spin(self._settings.tol_deg)
        tg.addWidget(self._tol_deg, 1, 1)
        tg.setColumnStretch(2, 1)
        outer.addWidget(tol_card)

        outer.addStretch(1)

        # --- Save ----------------------------------------------------------
        save = QPushButton("Save")
        save.setObjectName("primary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self._save)
        outer.addWidget(save)

    # --- small builders ----------------------------------------------------

    @staticmethod
    def _section_label(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("role", "section")
        return lbl

    @staticmethod
    def _card() -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    @staticmethod
    def _tol_spin(value: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setDecimals(3)
        spin.setRange(0.0, 1000.0)
        spin.setSingleStep(0.01)
        spin.setValue(value)
        spin.setFixedWidth(110)
        return spin

    def _save(self) -> None:
        new_state = SettingsState(
            moved=self._row_moved.current(),
            added=self._row_added.current(),
            removed=self._row_removed.current(),
            moved_from=self._row_moved_from.current(),
            write_json_report=self._json_check.isChecked(),
            write_excel_report=self._xlsx_check.isChecked(),
            tol_mm=self._tol_mm.value(),
            tol_deg=self._tol_deg.value(),
        )
        self._on_save(new_state)
