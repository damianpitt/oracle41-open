from __future__ import annotations

from collections.abc import Callable
from threading import Event, get_ident

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from oracle41_open.gui.task_runner import BackgroundTaskRunner


def _run_task(
    runner: BackgroundTaskRunner,
    operation: Callable[[], object],
    application: QApplication,
) -> None:
    completed = False
    event_loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)

    def on_finished() -> None:
        nonlocal completed
        completed = True
        event_loop.quit()

    runner.finished.connect(on_finished)
    timeout.timeout.connect(event_loop.quit)
    runner.start(operation)
    timeout.start(2_000)
    event_loop.exec()

    assert completed, "Background task did not finish before the timeout."


def test_task_runner_returns_result_on_gui_thread(qt_application: QApplication) -> None:
    runner = BackgroundTaskRunner()
    main_thread_id = get_ident()
    worker_thread_ids: list[int] = []
    result_events: list[tuple[object, int]] = []

    def operation() -> object:
        worker_thread_ids.append(get_ident())
        return "loaded"

    def on_result(result: object) -> None:
        result_events.append((result, get_ident()))

    runner.result.connect(on_result)
    _run_task(runner, operation, qt_application)

    assert len(worker_thread_ids) == 1
    assert worker_thread_ids[0] != main_thread_id
    assert result_events == [("loaded", main_thread_id)]


def test_task_runner_returns_error_and_finishes(qt_application: QApplication) -> None:
    runner = BackgroundTaskRunner()
    errors: list[object] = []

    def operation() -> object:
        raise RuntimeError("provider unavailable")

    runner.error.connect(errors.append)
    _run_task(runner, operation, qt_application)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "provider unavailable"


def test_task_runner_cancellation_suppresses_late_result(
    qt_application: QApplication,
) -> None:
    runner = BackgroundTaskRunner()
    started = Event()
    release = Event()
    results: list[object] = []
    errors: list[object] = []
    finished = Event()

    def operation() -> object:
        started.set()
        release.wait(timeout=2)
        return "late result"

    runner.result.connect(results.append)
    runner.error.connect(errors.append)
    runner.finished.connect(finished.set)
    runner.start(operation)
    while not started.wait(timeout=0.01):
        qt_application.processEvents()

    runner.cancel_all()
    release.set()
    while not finished.wait(timeout=0.01):
        qt_application.processEvents()
    qt_application.processEvents()

    assert results == []
    assert errors == []
