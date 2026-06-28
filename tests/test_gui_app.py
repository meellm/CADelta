"""App construction smoke tests: build the real Qt widgets headlessly.

Forces the offscreen Qt platform so these run on CI runners with no display.
They exercise widget construction, theme application, view routing, the
settings save round-trip, and the worker signal bridge without any real STEP
file or a running event loop.
"""
from __future__ import annotations

import os

# Must be set before the first Qt import so QApplication picks the headless
# platform plugin on CI machines without an X server / display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from pathlib import Path

from PySide6.QtCore import Qt

from cadelta.gui.app import MainWindow, build_app
from cadelta.gui.main_view import MainView, _looks_like_step
from cadelta.gui.qt_worker import DiffWorker, _SignalSink
from cadelta.gui.settings import SettingsState, StatusSetting
from cadelta.gui.settings_view import SettingsView
from cadelta.gui.worker import DiffJob, DoneMessage, ErrorMessage, PhaseMessage


@pytest.fixture(scope="module")
def app():
    """A single themed QApplication for the whole module (Qt forbids more
    than one)."""
    return build_app([])


def test_build_app_returns_themed_singleton(app):
    """build_app applies a non-empty style sheet and is idempotent: calling it
    again returns the same QApplication instance."""
    assert app.styleSheet()
    again = build_app([])
    assert again is app


def test_main_window_constructs_and_defaults_to_main_view(app):
    win = MainWindow()
    assert win.windowTitle() == "CADelta"
    assert win._stack.currentWidget() is win._main_view


def test_compare_button_disabled_until_both_files_chosen(app):
    view = MainView(SettingsState(), on_open_settings=lambda: None)
    assert view._compare_btn.isEnabled() is False

    # Simulate accepting a file in each zone (bypasses the file dialog).
    view._zone_v1._accept(Path("/tmp/v1.step"))
    assert view._compare_btn.isEnabled() is False  # only one side filled
    view._zone_v2._accept(Path("/tmp/v2.step"))
    assert view._compare_btn.isEnabled() is True


def test_drop_zone_accept_updates_label_and_path(app):
    view = MainView(SettingsState(), on_open_settings=lambda: None)
    view._zone_v1._accept(Path("/tmp/some_assembly.step"))
    assert view._zone_v1.path == Path("/tmp/some_assembly.step")
    assert "some_assembly.step" in view._zone_v1._label.text()


def test_new_compare_clears_stale_output_link(app, monkeypatch, tmp_path):
    """Starting a fresh compare drops the previous run's output link so the
    status label can't open the stale folder while the new diff runs."""
    view = MainView(SettingsState(), on_open_settings=lambda: None)
    view._zone_v1._accept(tmp_path / "v1.step")
    view._zone_v2._accept(tmp_path / "v2.step")

    # Simulate a finished previous run: status is a clickable link to a folder.
    view._on_succeeded(DoneMessage(out_step=tmp_path / "old_diff.step"))
    assert view._last_output == tmp_path / "old_diff.step"
    assert view._status.cursor().shape() == Qt.PointingHandCursor

    # Patch the save dialog and the worker launch so _on_compare runs headlessly
    # without a real thread or file picker.
    monkeypatch.setattr(
        "cadelta.gui.main_view.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "new_diff.step"), "")),
    )
    monkeypatch.setattr(MainView, "_start_worker", lambda self, job: None)

    view._on_compare()

    # Stale link state is gone; clicking the status now does nothing.
    assert view._last_output is None
    assert view._status.cursor().shape() == Qt.ArrowCursor


def test_looks_like_step_extension_filter():
    assert _looks_like_step("/x/a.step")
    assert _looks_like_step("/x/a.STP")
    assert not _looks_like_step("/x/a.txt")
    assert not _looks_like_step("/x/a")


def test_window_routes_to_settings_and_back_persists(app, monkeypatch):
    """Opening settings, editing a value, and saving routes back to the main
    view and persists the new state through the window's save hook.

    load/save are patched so the test neither reads nor writes the user's real
    ~/.cadelta/settings.json.
    """
    captured = {}
    monkeypatch.setattr("cadelta.gui.app.load_settings", lambda: SettingsState())
    monkeypatch.setattr("cadelta.gui.app.save_settings", lambda s: captured.update(s=s))

    win = MainWindow()
    win._show_settings()
    assert isinstance(win._stack.currentWidget(), SettingsView)

    sv = win._settings_view
    sv._tol_mm.setValue(0.25)
    sv._json_check.setChecked(True)
    sv._save()

    # Routed back to main; new state is live on the window and was persisted.
    assert win._stack.currentWidget() is win._main_view
    assert win._settings.tol_mm == 0.25
    assert win._settings.write_json_report is True
    assert captured["s"].tol_mm == 0.25


def test_settings_view_collects_row_state(app):
    state = SettingsState(
        added=StatusSetting(enabled=False, color=(0.1, 0.2, 0.3)),
    )
    saved = {}
    sv = SettingsView(state, on_save=lambda s: saved.update(s=s), on_back=lambda: None)
    sv._save()
    out = saved["s"]
    assert out.added.enabled is False
    # Color preserved through the swatch round-trip.
    assert all(abs(a - b) < 0.01 for a, b in zip(out.added.color, (0.1, 0.2, 0.3)))


def test_signal_sink_dispatches_each_message_type(app, tmp_path):
    """The Qt sink maps each worker message to the matching signal."""
    job = DiffJob(
        v1_path=tmp_path / "v1.step",
        v2_path=tmp_path / "v2.step",
        out_step=tmp_path / "out.step",
        settings=SettingsState(),
    )
    worker = DiffWorker(job)
    seen = {"phase": [], "done": [], "failed": []}
    worker.phase.connect(lambda t: seen["phase"].append(t))
    worker.succeeded.connect(lambda m: seen["done"].append(m))
    worker.failed.connect(lambda m: seen["failed"].append(m))

    sink = _SignalSink(worker)
    sink.put(PhaseMessage(text="Reading..."))
    sink.put(DoneMessage(out_step=job.out_step))
    sink.put(ErrorMessage(message="boom", exc=RuntimeError("boom")))

    assert seen["phase"] == ["Reading..."]
    assert len(seen["done"]) == 1 and seen["done"][0].out_step == job.out_step
    assert seen["failed"] == ["boom"]
