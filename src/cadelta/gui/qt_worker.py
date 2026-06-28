"""Qt bridge over the pure :func:`cadelta.gui.worker.run_diff_job` pipeline.

``run_diff_job`` pushes :class:`PhaseMessage` / :class:`DoneMessage` /
:class:`ErrorMessage` onto any object with a ``put`` method. This adapter
re-emits each message as a Qt signal. The worker runs on a :class:`QThread`,
and because the receiver lives on the main thread Qt delivers the connections
as queued (thread-safe) calls.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .worker import DiffJob, DoneMessage, ErrorMessage, PhaseMessage, run_diff_job


class _SignalSink:
    """Adapter for the ``put`` protocol ``run_diff_job`` expects: turns each
    message into the matching Qt signal on the owning worker."""

    def __init__(self, worker: "DiffWorker") -> None:
        self._worker = worker

    def put(self, msg: object) -> None:
        if isinstance(msg, PhaseMessage):
            self._worker.phase.emit(msg.text)
        elif isinstance(msg, DoneMessage):
            self._worker.succeeded.emit(msg)
        elif isinstance(msg, ErrorMessage):
            self._worker.failed.emit(msg.message)


class DiffWorker(QObject):
    """Runs one :class:`DiffJob` and reports progress via signals.

    Lifecycle (managed by the caller, see MainView):
        worker = DiffWorker(job); worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
    """

    phase = Signal(str)            # coarse status text
    succeeded = Signal(object)     # DoneMessage
    failed = Signal(str)           # user-facing error string
    finished = Signal()            # always emitted once, success or failure

    def __init__(self, job: DiffJob) -> None:
        super().__init__()
        self._job = job

    @Slot()
    def run(self) -> None:
        # run_diff_job never raises out, so try/finally is enough to guarantee
        # that `finished` always fires.
        try:
            run_diff_job(self._job, _SignalSink(self))
        finally:
            self.finished.emit()
