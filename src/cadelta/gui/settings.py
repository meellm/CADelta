"""GUI-side settings: tick state and color overrides per diff status, plus
output toggles and tolerance values. Persisted as JSON in the user's home
directory between runs.

The four per-status entries (``moved``, ``added``, ``removed``,
``moved_from``) carry the *user's* color choice. Whether that color is
applied when ``write_diff`` runs depends on the ``enabled`` field — see the
worker for the asymmetric tick semantics:

- MOVED/ADDED unticked → the writer is told to use the default color.
- REMOVED/MOVED_FROM unticked → the writer is told to omit those bodies
  from ``diff.step`` entirely.

Settings live at ``~/.cadelta/settings.json``. Missing file or malformed
JSON falls back to defaults silently — there's no migration story to
worry about for v0.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Tuple

from .defaults import DEFAULT_COLORS


# Where the settings file lives. Module-level so tests can monkeypatch it
# instead of poking at the user's real home dir.
SETTINGS_PATH = Path.home() / ".cadelta" / "settings.json"


RGB = Tuple[float, float, float]


@dataclass
class StatusSetting:
    """One row in the settings page: a tick + a color picker."""
    enabled: bool
    color: RGB

    def to_json(self) -> dict:
        # Lists round-trip through JSON cleanly; tuples don't (they come back
        # as lists and break equality checks).
        return {"enabled": self.enabled, "color": list(self.color)}

    @classmethod
    def from_json(cls, data: dict, default_color: RGB) -> "StatusSetting":
        # Defensive parsing: bad types fall back to defaults rather than
        # crashing the GUI on first launch after a hand-edit gone wrong.
        enabled = bool(data.get("enabled", True))
        raw_color = data.get("color", default_color)
        try:
            color = tuple(float(c) for c in raw_color)
            if len(color) != 3:
                raise ValueError
        except (TypeError, ValueError):
            color = default_color
        return cls(enabled=enabled, color=color)


@dataclass
class SettingsState:
    """Everything the GUI persists between runs.

    Defaults are constructed lazily because :data:`DEFAULT_COLORS` is itself
    derived from the writer module — keeping the defaults in one place
    makes the "untick reverts to writer's ship-time color" contract obvious.
    """
    moved: StatusSetting = field(default_factory=lambda: StatusSetting(True, DEFAULT_COLORS["moved"]))
    added: StatusSetting = field(default_factory=lambda: StatusSetting(True, DEFAULT_COLORS["added"]))
    removed: StatusSetting = field(default_factory=lambda: StatusSetting(True, DEFAULT_COLORS["removed"]))
    moved_from: StatusSetting = field(default_factory=lambda: StatusSetting(True, DEFAULT_COLORS["moved_from"]))
    write_json_report: bool = False
    write_excel_report: bool = False
    tol_mm: float = 0.01
    tol_deg: float = 0.01

    def to_json(self) -> dict:
        return {
            "moved": self.moved.to_json(),
            "added": self.added.to_json(),
            "removed": self.removed.to_json(),
            "moved_from": self.moved_from.to_json(),
            "write_json_report": self.write_json_report,
            "write_excel_report": self.write_excel_report,
            "tol_mm": self.tol_mm,
            "tol_deg": self.tol_deg,
        }

    @classmethod
    def from_json(cls, data: dict) -> "SettingsState":
        # `data.get(name, {})` keeps us tolerant of partial files written by
        # a previous version that didn't yet have all fields.
        return cls(
            moved=StatusSetting.from_json(data.get("moved", {}), DEFAULT_COLORS["moved"]),
            added=StatusSetting.from_json(data.get("added", {}), DEFAULT_COLORS["added"]),
            removed=StatusSetting.from_json(data.get("removed", {}), DEFAULT_COLORS["removed"]),
            moved_from=StatusSetting.from_json(data.get("moved_from", {}), DEFAULT_COLORS["moved_from"]),
            write_json_report=bool(data.get("write_json_report", False)),
            write_excel_report=bool(data.get("write_excel_report", False)),
            tol_mm=float(data.get("tol_mm", 0.01)),
            tol_deg=float(data.get("tol_deg", 0.01)),
        )


def load_settings(path: Path = SETTINGS_PATH) -> SettingsState:
    """Read settings from ``path`` or return defaults if the file is missing
    or malformed. Never raises — bad input is a UX problem, not a crash."""
    try:
        raw = path.read_text()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        return SettingsState()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return SettingsState()
    if not isinstance(data, dict):
        return SettingsState()
    return SettingsState.from_json(data)


def save_settings(state: SettingsState, path: Path = SETTINGS_PATH) -> None:
    """Persist ``state`` to ``path``, creating the parent directory as needed.

    Uses a write-then-rename pattern so a crash mid-write can't leave a
    truncated file that ``load_settings`` would silently accept as
    "malformed → reset to defaults" and erase the user's choices.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2))
    tmp.replace(path)
