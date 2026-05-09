"""Worker smoke tests — exercise the full diff pipeline through the GUI's
worker module without any Tk imports. Verifies the queue protocol is well
formed, settings translate to the right writer state, and the optional
JSON/Excel outputs land at the right paths.
"""
from __future__ import annotations

import queue
from pathlib import Path

from cadelta import writer
from cadelta.gui.defaults import DEFAULT_COLORS
from cadelta.gui.settings import SettingsState, StatusSetting
from cadelta.gui.worker import (
    DiffJob,
    DoneMessage,
    ErrorMessage,
    PhaseMessage,
    run_diff_job,
)
from cadelta.matcher import Status

from .conftest import box_at, make_step


def _build_pair(tmp_path: Path) -> tuple[Path, Path]:
    v1 = tmp_path / "v1.step"
    v2 = tmp_path / "v2.step"
    make_step(v1, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40, 0, 0)),
        ("C", box_at(20, 20, 20, 80, 0, 0)),
    ])
    make_step(v2, [
        ("A", box_at(10, 10, 10, 0, 0, 0)),
        ("B", box_at(15, 15, 15, 40.5, 0, 0)),
        ("D", box_at(12, 12, 12, 120, 0, 0)),
    ])
    return v1, v2


def _drain(q: "queue.Queue") -> list:
    msgs = []
    while not q.empty():
        msgs.append(q.get_nowait())
    return msgs


def test_worker_runs_diff_and_emits_done_message(tmp_path: Path):
    """Happy path: worker finishes, queue ends with a DoneMessage carrying
    the output STEP path and counts. PhaseMessage flows through in order."""
    v1, v2 = _build_pair(tmp_path)
    out_step = tmp_path / "diff.step"

    q: "queue.Queue" = queue.Queue()
    job = DiffJob(v1_path=v1, v2_path=v2, out_step=out_step, settings=SettingsState())
    run_diff_job(job, q)  # synchronous call — easier to assert in tests

    msgs = _drain(q)
    # At least the four phases (read v1, read v2, diff, write).
    phase_texts = [m.text for m in msgs if isinstance(m, PhaseMessage)]
    assert any("v1.step" in t for t in phase_texts)
    assert any("v2.step" in t for t in phase_texts)
    assert any("Computing diff" in t for t in phase_texts)
    assert any("diff.step" in t for t in phase_texts)

    done = [m for m in msgs if isinstance(m, DoneMessage)]
    assert len(done) == 1, f"expected exactly one DoneMessage; got messages: {msgs}"
    d = done[0]
    assert d.out_step == out_step
    assert out_step.exists() and out_step.stat().st_size > 0
    assert d.counts is not None
    # Sanity on counts — at minimum we have one moved + one removed + one added.
    assert d.counts[Status.UNCHANGED.value] >= 1
    assert d.counts[Status.MOVED.value] == 1
    assert d.counts[Status.ADDED.value] == 1
    assert d.counts[Status.REMOVED.value] == 1


def test_worker_writes_json_and_excel_when_enabled(tmp_path: Path):
    """When the settings flags are on, the worker writes _report.json and
    _report.xlsx next to the STEP, and reports their paths in DoneMessage."""
    v1, v2 = _build_pair(tmp_path)
    out_step = tmp_path / "diff.step"

    q: "queue.Queue" = queue.Queue()
    settings = SettingsState(write_json_report=True, write_excel_report=True)
    job = DiffJob(v1_path=v1, v2_path=v2, out_step=out_step, settings=settings)
    run_diff_job(job, q)

    msgs = _drain(q)
    done = [m for m in msgs if isinstance(m, DoneMessage)][0]
    assert done.out_json is not None and done.out_json.exists()
    assert done.out_xlsx is not None and done.out_xlsx.exists()
    assert done.out_json.name == "diff_report.json"
    assert done.out_xlsx.name == "diff_report.xlsx"


def test_worker_omits_removed_and_ghost_when_unticked(tmp_path: Path):
    """REMOVED and MOVED_FROM ticks off → those bodies are absent from the
    output STEP. The writer-flag tests already cover the engine wiring; this
    test pins down that the worker correctly translates SettingsState into
    those flags."""
    from cadelta.reader import load_parts

    v1, v2 = _build_pair(tmp_path)
    out_step = tmp_path / "diff.step"

    settings = SettingsState(
        removed=StatusSetting(enabled=False, color=DEFAULT_COLORS["removed"]),
        moved_from=StatusSetting(enabled=False, color=DEFAULT_COLORS["moved_from"]),
    )
    q: "queue.Queue" = queue.Queue()
    job = DiffJob(v1_path=v1, v2_path=v2, out_step=out_step, settings=settings)
    run_diff_job(job, q)

    parts = load_parts(out_step)
    names = [p.name for p in parts]
    assert not any("REMOVED" in n for n in names), (
        f"REMOVED should be excluded; got {names}"
    )
    assert not any("MOVED_FROM" in n for n in names), (
        f"MOVED_FROM should be excluded; got {names}"
    )


def test_worker_applies_custom_color_when_ticked(tmp_path: Path):
    """ADDED ticked + custom color → the rendered ADDED body carries that
    color. Restore the writer's default afterwards because the dict is a
    shared singleton."""
    from cadelta.reader import load_parts

    sentinel = (0.10, 0.90, 0.20)  # bright green
    v1, v2 = _build_pair(tmp_path)
    out_step = tmp_path / "diff.step"

    settings = SettingsState(
        added=StatusSetting(enabled=True, color=sentinel),
    )
    q: "queue.Queue" = queue.Queue()
    job = DiffJob(v1_path=v1, v2_path=v2, out_step=out_step, settings=settings)

    original_added = writer.COLOR_BY_STATUS[Status.ADDED]
    try:
        run_diff_job(job, q)
        parts = load_parts(out_step)
        added = next(p for p in parts if "ADDED" in p.name)
        for got, want in zip(added.color, sentinel):
            assert abs(got - want) < 0.02, (
                f"ADDED should carry sentinel color {sentinel}; got {added.color}"
            )
    finally:
        writer.COLOR_BY_STATUS[Status.ADDED] = original_added


def test_worker_emits_error_message_on_unreadable_step(tmp_path: Path):
    """Bad STEP file → worker doesn't crash; queue ends with ErrorMessage,
    not DoneMessage. The Tk thread relies on this contract to re-enable the
    Compare button on failure."""
    bad = tmp_path / "broken.step"
    bad.write_text("this is definitely not a STEP file")
    good_v2 = tmp_path / "v2.step"
    make_step(good_v2, [("A", box_at(10, 10, 10))])

    out_step = tmp_path / "diff.step"
    q: "queue.Queue" = queue.Queue()
    job = DiffJob(v1_path=bad, v2_path=good_v2, out_step=out_step, settings=SettingsState())
    run_diff_job(job, q)

    msgs = _drain(q)
    errors = [m for m in msgs if isinstance(m, ErrorMessage)]
    dones = [m for m in msgs if isinstance(m, DoneMessage)]
    assert len(errors) == 1, f"expected one ErrorMessage; got {msgs}"
    assert len(dones) == 0, "Done should not fire when read fails"
    # The user-facing message should at least mention reading or the path.
    assert errors[0].message  # non-empty
