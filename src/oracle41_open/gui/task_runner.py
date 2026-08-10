from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot


class _TaskSignals(QObject):
    result = Signal(int, object)
    error = Signal(int, object)
    finished = Signal(int)


@dataclass
class _TaskState:
    task_id: int
    is_canceled: bool = False


class _BackgroundTask(QRunnable):
    def __init__(self, operation: Callable[[], object], state: _TaskState) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._operation = operation
        self._state = state
        self.signals = _TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self._state.is_canceled:
                return
            result = self._operation()
        except Exception as error:
            self.signals.error.emit(self._state.task_id, error)
        else:
            self.signals.result.emit(self._state.task_id, result)
        finally:
            self.signals.finished.emit(self._state.task_id)


class BackgroundTaskRunner(QObject):
    """Run one-shot service operations without blocking the Qt event loop."""

    result = Signal(object)
    error = Signal(object)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._next_task_id = 1
        self._task_states: dict[int, _TaskState] = {}
        self._active_tasks: dict[int, _BackgroundTask] = {}

    def start(self, operation: Callable[[], object]) -> None:
        task_id = self._next_task_id
        self._next_task_id += 1
        state = _TaskState(task_id=task_id)
        task = _BackgroundTask(operation, state)
        queued = Qt.ConnectionType.QueuedConnection
        task.signals.result.connect(self._forward_result, queued)
        task.signals.error.connect(self._forward_error, queued)
        task.signals.finished.connect(self._forget_task, queued)
        task.signals.finished.connect(self._forward_finished, queued)
        self._task_states[task_id] = state
        self._active_tasks[task_id] = task
        self._pool.start(task)

    def cancel_all(self) -> None:
        for state in self._task_states.values():
            state.is_canceled = True

    @Slot(int, object)
    def _forward_result(self, task_id: int, result: object) -> None:
        state = self._task_states.get(task_id)
        if state is not None and not state.is_canceled:
            self.result.emit(result)

    @Slot(int, object)
    def _forward_error(self, task_id: int, error: object) -> None:
        state = self._task_states.get(task_id)
        if state is not None and not state.is_canceled:
            self.error.emit(error)

    @Slot(int)
    def _forward_finished(self, _task_id: int) -> None:
        self.finished.emit()

    @Slot(int)
    def _forget_task(self, task_id: int) -> None:
        self._task_states.pop(task_id, None)
        self._active_tasks.pop(task_id, None)
