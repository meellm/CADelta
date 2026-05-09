"""Settings round-trip + tolerance tests for :mod:`cadelta.gui.settings`.

No Tk imports — these are pure-Python tests on the persistence layer the GUI
relies on between runs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cadelta.gui.defaults import DEFAULT_COLORS
from cadelta.gui.settings import (
    SettingsState,
    StatusSetting,
    load_settings,
    save_settings,
)


def test_settings_defaults_match_writer_defaults():
    """Brand-new SettingsState pulls colors from the writer's ship-time
    defaults (so 'untick' restores those exact RGB triples)."""
    state = SettingsState()
    assert state.moved.color == DEFAULT_COLORS["moved"]
    assert state.added.color == DEFAULT_COLORS["added"]
    assert state.removed.color == DEFAULT_COLORS["removed"]
    assert state.moved_from.color == DEFAULT_COLORS["moved_from"]
    assert state.tol_mm == 0.01
    assert state.tol_deg == 0.01
    assert state.write_json_report is False
    assert state.write_excel_report is False


def test_settings_roundtrip(tmp_path: Path):
    """Write a SettingsState to disk, load it back, equality holds."""
    path = tmp_path / "settings.json"
    state = SettingsState(
        moved=StatusSetting(enabled=False, color=(0.2, 0.4, 0.6)),
        added=StatusSetting(enabled=True, color=(0.1, 0.9, 0.3)),
        removed=StatusSetting(enabled=False, color=(0.5, 0.5, 0.5)),
        moved_from=StatusSetting(enabled=True, color=(0.99, 0.10, 0.50)),
        write_json_report=True,
        write_excel_report=True,
        tol_mm=0.05,
        tol_deg=0.20,
    )
    save_settings(state, path)
    loaded = load_settings(path)
    assert loaded == state


def test_settings_load_missing_file_returns_defaults(tmp_path: Path):
    """No settings file yet → caller gets a fresh defaults object, no
    exception, no logging spam."""
    path = tmp_path / "does_not_exist.json"
    state = load_settings(path)
    assert state == SettingsState()


def test_settings_load_malformed_json_returns_defaults(tmp_path: Path):
    """Hand-edited file got corrupted → defaults rather than a crash."""
    path = tmp_path / "broken.json"
    path.write_text("this is not json {")
    state = load_settings(path)
    assert state == SettingsState()


def test_settings_load_partial_dict_uses_defaults_for_missing_keys(tmp_path: Path):
    """A settings file written by an older app version that doesn't have
    every field yet must still load — missing keys fall back to defaults
    so we never break forward-compat on a minor schema change."""
    path = tmp_path / "partial.json"
    # Only tol_mm specified; everything else should default.
    path.write_text('{"tol_mm": 0.5}')
    state = load_settings(path)
    assert state.tol_mm == 0.5
    assert state.tol_deg == 0.01  # default
    assert state.moved.color == DEFAULT_COLORS["moved"]


def test_settings_save_uses_atomic_replace(tmp_path: Path):
    """Crash-safety: save_settings goes through a .tmp file then renames.
    After save completes, only the final file is present (the .tmp is gone).
    """
    path = tmp_path / "settings.json"
    state = SettingsState()
    save_settings(state, path)
    assert path.exists()
    assert not path.with_suffix(path.suffix + ".tmp").exists(), (
        "the .tmp file should have been renamed away"
    )


def test_status_setting_color_recovers_from_garbage(tmp_path: Path):
    """A truncated/corrupted color value falls back to the supplied default
    rather than raising — important so a user's typo in their hand-edited
    settings doesn't brick the GUI."""
    parsed = StatusSetting.from_json(
        {"enabled": True, "color": "not-a-list"},
        default_color=DEFAULT_COLORS["moved"],
    )
    assert parsed.color == DEFAULT_COLORS["moved"]

    parsed_short = StatusSetting.from_json(
        {"enabled": True, "color": [1.0, 0.0]},  # only 2 channels
        default_color=DEFAULT_COLORS["moved"],
    )
    assert parsed_short.color == DEFAULT_COLORS["moved"]
