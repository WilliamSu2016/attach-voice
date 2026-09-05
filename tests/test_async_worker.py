"""tests/test_async_worker.py — src/utils/async_worker.py 的单测。

`WorkerThread` 依赖 `PyQt6.QtCore.QThread`/`pyqtSignal`，需要一个真实的 `QCoreApplication`
事件循环来传递跨线程信号（Qt 信号槽机制本身依赖事件循环），但不涉及任何 Widget/窗口，
因此不受 ARCHITECTURE.md「GUI 不做自动化测试」限制（该限制针对 `src/gui/**` 的界面代码）。
"""
from __future__ import annotations

import time

import pytest
from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer

from src.utils.async_worker import CancelToken, TaskCancelledError, WorkerThread


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance() or QCoreApplication([])
    return app


def _run_worker_and_wait(worker: WorkerThread, timeout_ms: int = 5000):
    """启动 worker 并跑一个局部事件循环，直到 finished 或 error 触发（或超时）。"""
    loop = QEventLoop()
    results = {}

    def on_finished(value):
        results["finished"] = value
        loop.quit()

    def on_error(exc):
        results["error"] = exc
        loop.quit()

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    QTimer.singleShot(timeout_ms, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(2000)
    return results


def test_worker_thread_success_emits_finished_with_result(qapp):
    def target(a, b, on_progress, cancel_token):
        on_progress(0.5)
        on_progress(1.0)
        return a + b

    worker = WorkerThread(target, 2, 3)
    results = _run_worker_and_wait(worker)

    assert results.get("finished") == 5
    assert "error" not in results


def test_worker_thread_progress_signal_reports_values(qapp):
    reported = []

    def target(on_progress, cancel_token):
        on_progress(0.25)
        on_progress(0.75)
        return "done"

    worker = WorkerThread(target)
    worker.progress.connect(reported.append)
    _run_worker_and_wait(worker)

    assert reported == [pytest.approx(0.25), pytest.approx(0.75)]


def test_worker_thread_exception_emits_error_signal_with_exception_instance(qapp):
    def target(on_progress, cancel_token):
        raise ValueError("boom")

    worker = WorkerThread(target)
    results = _run_worker_and_wait(worker)

    assert "finished" not in results
    assert isinstance(results.get("error"), ValueError)
    assert str(results["error"]) == "boom"


def test_worker_thread_cancel_sets_cancel_token_and_target_can_raise_task_cancelled(qapp):
    def target(on_progress, cancel_token):
        for _ in range(50):
            if cancel_token.is_cancelled():
                raise TaskCancelledError("已取消")
            time.sleep(0.02)
        return "should not reach here"

    worker = WorkerThread(target)
    loop = QEventLoop()
    results = {}

    def on_error(exc):
        results["error"] = exc
        loop.quit()

    worker.error.connect(on_error)
    worker.start()
    QTimer.singleShot(100, worker.cancel)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    worker.wait(2000)

    assert isinstance(results.get("error"), TaskCancelledError)
    assert worker.cancel_token.is_cancelled() is True


def test_cancel_token_duck_types_is_cancelled_and_cancel():
    token = CancelToken()
    assert token.is_cancelled() is False
    token.cancel()
    assert token.is_cancelled() is True
