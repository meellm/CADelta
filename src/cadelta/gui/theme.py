"""Tokenized dark theme for the CADelta desktop GUI.

Everything visual flows from :data:`TOKENS`, a flat dict of named color/metric
strings, so widgets never hardcode hex values. :func:`build_qss` turns the
tokens into a Qt style sheet applied once at the QApplication level.

Palette: neutral dark surfaces with a steel-blue primary accent. The CAD diff
colors are reserved for status swatches so the chrome never competes with them.
"""
from __future__ import annotations

from typing import Dict


# Flat token table, referenced by name in build_qss and by widgets that need a
# raw color (e.g. painting a swatch). Hex strings only; derived shades are
# pre-resolved here so there is a single source of truth.
TOKENS: Dict[str, str] = {
    # Base surfaces, darkest to lightest.
    "bg": "#1b1d21",            # window background
    "surface": "#23262b",       # cards, panels
    "surface_raised": "#2b2f35",  # inputs, hovered surfaces
    "drop": "#202327",          # idle drop zone fill
    "drop_active": "#1f3340",   # drop zone while a file hovers over it

    # Lines and borders.
    "border": "#363a41",
    "border_strong": "#454b54",
    "divider": "#2e3238",

    # Text.
    "text": "#e6e8eb",
    "text_muted": "#9aa1aa",
    "text_faint": "#6b7280",
    "text_on_primary": "#ffffff",

    # Primary accent (steel blue) + interaction shades.
    "primary": "#3d7fd6",
    "primary_hover": "#4d8fe6",
    "primary_pressed": "#2f6dbf",
    "primary_disabled": "#33414f",

    # CAD diff accents, used for status swatches and the success link.
    "diff_added": "#26c6da",     # cyan
    "diff_moved": "#e6c84b",     # yellow
    "diff_removed": "#e0524a",   # red
    "diff_ghost": "#d6489b",     # pink (MOVED_FROM ghost)

    # Feedback.
    "success": "#3fbf7f",
    "danger": "#e0524a",

    # Metrics (px, as plain numbers in the strings below).
    "radius": "8",
    "radius_sm": "6",
    "pad": "10",
}


def color(name: str) -> str:
    """Return a raw token value, raising KeyError on an unknown name so typos
    fail loudly rather than rendering transparent."""
    return TOKENS[name]


def build_qss(tokens: Dict[str, str] = TOKENS) -> str:
    """Build the application-wide Qt style sheet from ``tokens``.

    A pure function of its input, so it can be reused for a light variant or
    asserted on in tests. Button variants are selected by ``objectName`` and
    dynamic properties rather than per-widget styling.
    """
    t = tokens
    return f"""
* {{
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
    color: {t['text']};
}}

QWidget#root, QMainWindow {{
    background: {t['bg']};
}}

QLabel {{
    background: transparent;
}}

QLabel[role="title"] {{
    font-size: 16px;
    font-weight: 600;
    color: {t['text']};
}}

QLabel[role="section"] {{
    font-size: 13px;
    font-weight: 600;
    color: {t['text']};
}}

QLabel[role="muted"] {{
    color: {t['text_muted']};
}}

QLabel[role="hint"] {{
    color: {t['text_faint']};
    font-size: 12px;
}}

QLabel[role="link"] {{
    color: {t['diff_added']};
    font-weight: 600;
}}

/* --- Cards ------------------------------------------------------------ */
QFrame#card {{
    background: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: {t['radius']}px;
}}

QFrame[role="divider"] {{
    background: {t['divider']};
    max-height: 1px;
    min-height: 1px;
    border: none;
}}

/* --- Drop zones ------------------------------------------------------- */
QFrame#dropZone {{
    background: {t['drop']};
    border: 2px dashed {t['border_strong']};
    border-radius: {t['radius']}px;
}}

QFrame#dropZone[active="true"] {{
    background: {t['drop_active']};
    border: 2px dashed {t['primary']};
}}

QFrame#dropZone[filled="true"] {{
    border: 2px solid {t['border']};
}}

/* --- Buttons ---------------------------------------------------------- */
QPushButton {{
    background: {t['surface_raised']};
    color: {t['text']};
    border: 1px solid {t['border_strong']};
    border-radius: {t['radius_sm']}px;
    padding: 7px 16px;
}}

QPushButton:hover {{
    background: {t['border']};
}}

QPushButton:pressed {{
    background: {t['surface']};
}}

QPushButton:disabled {{
    color: {t['text_faint']};
    background: {t['surface']};
    border-color: {t['border']};
}}

QPushButton#primary {{
    background: {t['primary']};
    color: {t['text_on_primary']};
    border: 1px solid {t['primary']};
    font-weight: 600;
}}

QPushButton#primary:hover {{
    background: {t['primary_hover']};
    border-color: {t['primary_hover']};
}}

QPushButton#primary:pressed {{
    background: {t['primary_pressed']};
    border-color: {t['primary_pressed']};
}}

QPushButton#primary:disabled {{
    background: {t['primary_disabled']};
    border-color: {t['primary_disabled']};
    color: {t['text_faint']};
}}

QPushButton#ghost {{
    background: transparent;
    border: none;
    color: {t['text_muted']};
    padding: 6px 10px;
}}

QPushButton#ghost:hover {{
    color: {t['text']};
    background: {t['surface_raised']};
}}

QPushButton#iconButton {{
    background: transparent;
    border: none;
    color: {t['text_muted']};
    font-size: 18px;
    padding: 4px 8px;
}}

QPushButton#iconButton:hover {{
    color: {t['text']};
    background: {t['surface_raised']};
    border-radius: {t['radius_sm']}px;
}}

QPushButton#swatch {{
    border: 1px solid {t['border_strong']};
    border-radius: {t['radius_sm']}px;
    min-width: 34px;
    max-width: 34px;
    min-height: 20px;
    max-height: 20px;
}}

/* --- Inputs ----------------------------------------------------------- */
QLineEdit, QDoubleSpinBox {{
    background: {t['surface_raised']};
    border: 1px solid {t['border_strong']};
    border-radius: {t['radius_sm']}px;
    padding: 5px 8px;
    selection-background-color: {t['primary']};
}}

QLineEdit:focus, QDoubleSpinBox:focus {{
    border-color: {t['primary']};
}}

QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    width: 16px;
    background: {t['surface']};
    border-left: 1px solid {t['border']};
}}

/* --- Check boxes ------------------------------------------------------ */
QCheckBox {{
    spacing: 8px;
    background: transparent;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {t['border_strong']};
    border-radius: 4px;
    background: {t['surface_raised']};
}}

QCheckBox::indicator:checked {{
    background: {t['primary']};
    border-color: {t['primary']};
    image: none;
}}

QCheckBox::indicator:hover {{
    border-color: {t['primary']};
}}

/* --- Progress --------------------------------------------------------- */
QProgressBar {{
    background: {t['surface_raised']};
    border: 1px solid {t['border']};
    border-radius: {t['radius_sm']}px;
    height: 8px;
    text-align: center;
}}

QProgressBar::chunk {{
    background: {t['primary']};
    border-radius: {t['radius_sm']}px;
}}
"""


def apply_theme(app, tokens: Dict[str, str] = TOKENS) -> None:
    """Apply the dark theme to a QApplication: Fusion base style, a dark
    palette (so native non-QSS widgets like menus follow), and the QSS."""
    # Imported lazily so the module imports cleanly without a display.
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication

    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(tokens["bg"]))
    pal.setColor(QPalette.Base, QColor(tokens["surface_raised"]))
    pal.setColor(QPalette.AlternateBase, QColor(tokens["surface"]))
    pal.setColor(QPalette.Text, QColor(tokens["text"]))
    pal.setColor(QPalette.WindowText, QColor(tokens["text"]))
    pal.setColor(QPalette.Button, QColor(tokens["surface_raised"]))
    pal.setColor(QPalette.ButtonText, QColor(tokens["text"]))
    pal.setColor(QPalette.Highlight, QColor(tokens["primary"]))
    pal.setColor(QPalette.HighlightedText, QColor(tokens["text_on_primary"]))
    pal.setColor(QPalette.ToolTipBase, QColor(tokens["surface"]))
    pal.setColor(QPalette.ToolTipText, QColor(tokens["text"]))
    pal.setColor(QPalette.PlaceholderText, QColor(tokens["text_faint"]))
    if isinstance(app, QApplication):
        app.setPalette(pal)

    app.setStyleSheet(build_qss(tokens))
