from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class _TaskSignals(QObject):
    result = Signal(object, object)
    error = Signal(object, object)
    finished = Signal(object)


class _BackgroundTask(QRunnable):
    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self._operation = operation
        self.signals = _TaskSignals()
        self._is_canceled = False

    @property
    def is_canceled(self) -> bool:
        return self._is_canceled

    def cancel(self) -> None:
        self._is_canceled = True

    @Slot()
    def run(self) -> None:
        try:
            if self._is_canceled:
                return
            result = self._operation()
        except Exception as error:
            self.signals.error.emit(self, error)
        else:
            self.signals.result.emit(self, result)
        finally:
            self.signals.finished.emit(self)


class BackgroundTaskRunner(QObject):
    """Run one-shot service operations without blocking the Qt event loop."""

    result = Signal(object)
    error = Signal(object)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._active_tasks: set[_BackgroundTask] = set()

    def start(self, operation: Callable[[], object]) -> None:
        task = _BackgroundTask(operation)
        task.signals.result.connect(self._forward_result)
        task.signals.error.connect(self._forward_error)
        task.signals.finished.connect(self._forward_finished)
        task.signals.finished.connect(self._forget_task)
        self._active_tasks.add(task)
        self._pool.start(task)

    def cancel_all(self) -> None:
        for task in self._active_tasks:
            task.cancel()

    @Slot(object, object)
    def _forward_result(self, task: object, result: object) -> None:
        if isinstance(task, _BackgroundTask) and not task.is_canceled:
            self.result.emit(result)

    @Slot(object, object)
    def _forward_error(self, task: object, error: object) -> None:
        if isinstance(task, _BackgroundTask) and not task.is_canceled:
            self.error.emit(error)

    @Slot(object)
    def _forward_finished(self, _task: object) -> None:
        self.finished.emit()

    @Slot(object)
    def _forget_task(self, task: object) -> None:
        if isinstance(task, _BackgroundTask):
            self._active_tasks.discard(task)
