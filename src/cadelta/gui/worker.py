"""Threaded diff runner for the GUI.

The diff goes off the Tk main thread so the window stays responsive and the
indeterminate progress bar keeps animating. The worker pushes phase
notifications and the final success/error onto a :class:`queue.Queue`; the
main thread polls it via ``root.after(...)`` and updates widgets from there.
**Never touch Tk widgets from the worker thread** — Tk is not thread-safe.

Phase messages are coarse: read v1, read v2, diff, write. The engine has no
finer-grained checkpoints, and refactoring it for a progress callback was
explicitly scoped out — indeterminate is honest about that.
"""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cadelta import writer
from cadelta.matcher import Status, diff_parts
from cadelta.reader import load_parts, load_parts_with_doc
from cadelta.writer import write_diff

from .defaults import DEFAULT_COLORS
from .excel_report import write_excel_report
from .settings import SettingsState


# --- Queue message protocol --------------------------------------------------
# The worker only ever pushes one of these dataclasses onto the queue. The Tk
# poller pattern-matches on `type(msg)` to decide what to do.

@dataclass
class PhaseMessage:
    """Coarse status update — what the worker is doing right now."""
    text: str


@dataclass
class DoneMessage:
    """Worker finished successfully. ``out_step`` is the path written."""
    out_step: Path
    out_json: Optional[Path] = None
    out_xlsx: Optional[Path] = None
    counts: Optional[dict] = None


@dataclass
class ErrorMessage:
    """Worker raised — message is a user-facing string. The original
    exception is captured for logging but not rendered to the user."""
    message: str
    exc: BaseException


# --- Job description ---------------------------------------------------------

@dataclass
class DiffJob:
    """Everything the worker needs to do one Compare run.

    Constructed on the main thread from the GUI's current state, then handed
    off to :func:`run_diff_job`. The worker is otherwise stateless.
    """
    v1_path: Path
    v2_path: Path
    out_step: Path
    settings: SettingsState


# --- Color/flag bridging -----------------------------------------------------

def _apply_settings_to_writer(state: SettingsState) -> tuple[bool, bool]:
    """Translate ``SettingsState`` into mutated writer module state +
    ``write_diff`` flags. Returns ``(include_removed, include_moved_ghost)``
    so the caller can pass them through.

    Per the user spec, the four ticks have asymmetric meaning:

    - MOVED / ADDED unticked → use the writer's default color for that
      status (still rendered in the diff).
    - REMOVED / MOVED_FROM unticked → omit those bodies from the output
      entirely (handled by passing ``False`` for the matching flag).

    For the rendered statuses, "ticked" means "use the user's picked
    color" and "unticked" means "use the default we shipped with."
    """
    # Direct dict assignment: writer.py reads these per call, no caching.
    writer.COLOR_BY_STATUS[Status.MOVED] = (
        state.moved.color if state.moved.enabled else DEFAULT_COLORS["moved"]
    )
    writer.COLOR_BY_STATUS[Status.ADDED] = (
        state.added.color if state.added.enabled else DEFAULT_COLORS["added"]
    )
    # REMOVED is rendered (red) when included; the user's picked color
    # applies if enabled, default red otherwise.
    writer.COLOR_BY_STATUS[Status.REMOVED] = (
        state.removed.color if state.removed.enabled else DEFAULT_COLORS["removed"]
    )
    # COLOR_MOVED_FROM is a module-level tuple (not a dict entry), so we
    # rebind the attribute on the module rather than mutating in place.
    # writer.py reads it via the module-global name on every call, so the
    # rebind is visible — verified by the writer-flag tests.
    writer.COLOR_MOVED_FROM = (
        state.moved_from.color if state.moved_from.enabled else DEFAULT_COLORS["moved_from"]
    )

    return state.removed.enabled, state.moved_from.enabled


# --- Auxiliary report writers ------------------------------------------------

def _write_json_report(
    out_path: Path,
    *,
    v1_path: Path,
    v2_path: Path,
    tol_mm: float,
    tol_deg: float,
    result,
) -> None:
    """JSON shape mirrors :mod:`cadelta.cli`'s ``--report`` output so users
    can flip between CLI and GUI without their downstream tooling changing.
    """
    counts = {s.value: len(result.by_status(s)) for s in Status}
    data = {
        "v1": str(v1_path),
        "v2": str(v2_path),
        "tol_mm": tol_mm,
        "tol_deg": tol_deg,
        "counts": counts,
        "entries": [
            {
                "status": e.status.value,
                "name": (e.part_v2.name if e.part_v2 else (e.part_v1.name if e.part_v1 else "")),
                "delta_mm": e.delta_mm,
                "delta_deg": e.delta_deg,
            }
            for e in result.entries
        ],
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2))


def _derive_report_paths(out_step: Path, state: SettingsState) -> tuple[Optional[Path], Optional[Path]]:
    """Given the user's chosen STEP path, derive the JSON/Excel report
    paths next to it. Both share the STEP's stem with a ``_report``
    suffix so a folder of multiple diffs stays sortable by base name."""
    json_path = out_step.with_name(f"{out_step.stem}_report.json") if state.write_json_report else None
    xlsx_path = out_step.with_name(f"{out_step.stem}_report.xlsx") if state.write_excel_report else None
    return json_path, xlsx_path


# --- Worker entry point ------------------------------------------------------

def run_diff_job(job: DiffJob, q: "queue.Queue") -> None:
    """Run one diff end-to-end, pushing progress + result messages onto ``q``.

    Designed to be called as the target of ``threading.Thread`` — never
    raises out (any exception becomes an :class:`ErrorMessage`). The GUI
    thread is responsible for re-enabling its widgets when it sees a
    DoneMessage or ErrorMessage on the queue.
    """
    try:
        include_removed, include_moved_ghost = _apply_settings_to_writer(job.settings)

        q.put(PhaseMessage(text=f"Reading {job.v1_path.name}…"))
        parts_v1 = load_parts(job.v1_path)

        q.put(PhaseMessage(text=f"Reading {job.v2_path.name}…"))
        parts_v2, doc_v2 = load_parts_with_doc(job.v2_path)

        q.put(PhaseMessage(text="Computing diff…"))
        result = diff_parts(
            parts_v1, parts_v2,
            tol_mm=job.settings.tol_mm,
            tol_deg=job.settings.tol_deg,
        )

        q.put(PhaseMessage(text=f"Writing {job.out_step.name}…"))
        write_diff(
            result,
            job.out_step,
            doc_v2=doc_v2,
            include_removed=include_removed,
            include_moved_ghost=include_moved_ghost,
        )

        json_path, xlsx_path = _derive_report_paths(job.out_step, job.settings)
        if json_path is not None:
            q.put(PhaseMessage(text=f"Writing {json_path.name}…"))
            _write_json_report(
                json_path,
                v1_path=job.v1_path, v2_path=job.v2_path,
                tol_mm=job.settings.tol_mm, tol_deg=job.settings.tol_deg,
                result=result,
            )
        if xlsx_path is not None:
            q.put(PhaseMessage(text=f"Writing {xlsx_path.name}…"))
            write_excel_report(
                result, xlsx_path,
                v1_path=job.v1_path, v2_path=job.v2_path,
                tol_mm=job.settings.tol_mm, tol_deg=job.settings.tol_deg,
            )

        counts = {s.value: len(result.by_status(s)) for s in Status}
        q.put(DoneMessage(
            out_step=job.out_step,
            out_json=json_path,
            out_xlsx=xlsx_path,
            counts=counts,
        ))

    except RuntimeError as exc:
        # Engine surfaces unreadable / malformed STEP files as RuntimeError;
        # show its message verbatim — it's already user-friendly enough.
        q.put(ErrorMessage(message=str(exc), exc=exc))
    except Exception as exc:  # pragma: no cover — defensive catch-all
        q.put(ErrorMessage(
            message=f"Unexpected error: {type(exc).__name__}: {exc}",
            exc=exc,
        ))


def start_worker(job: DiffJob, q: "queue.Queue") -> threading.Thread:
    """Spawn a daemon thread to run ``job``. Returned thread can be joined
    by the caller if needed; the daemon flag means it won't block process
    exit if the user closes the window mid-diff.
    """
    t = threading.Thread(target=run_diff_job, args=(job, q), daemon=True)
    t.start()
    return t
