"""QThread 封装：桥接 edge-tts 的 asyncio 调用与 ffmpeg 子进程，供 GUI 层异步调用 core 函数。

依据 ARCHITECTURE.md 规则 B4：本模块是唯一允许同时接触 `PyQt6.QtCore.QThread` 与
core 层的适配器，且**不含业务逻辑**——`WorkerThread` 只负责在后台线程运行调用方传入的
任意可调用对象，并把执行结果/异常/进度转换为 Qt 信号；它不了解 TTS/ffmpeg 等具体业务
语义，目标函数（`target`）及其内部逻辑完全由调用方（`src/gui/*`）提供。

约定：`target` 必须接受关键字参数 `on_progress: Callable[[float], None]` 与
`cancel_token: CancelToken`（与 `src.core.video_processor.export`/`export_audio_only`
已有的参数约定保持一致，便于直接把 core 层函数当作 `target` 传入）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from PyQt6.QtCore import QThread, pyqtSignal


class TaskCancelledError(RuntimeError):
    """通用任务取消异常：当 `target` 内部没有更具体的领域取消异常（如
    `video_processor.ExportCancelledError`）时，可抛出本异常表达"已按取消令牌中断"。
    """


class CancelToken:
    """线程安全的取消令牌（基于 `threading.Event`）。

    与 `src.core.video_processor.CancelToken` 接口一致（`cancel()`/`is_cancelled()`），
    但本模块属于 `utils` 层，依据依赖方向规则（B3：utils 不得 import core）不能直接复用
    `core` 层的定义，因此独立实现一份；两者可互相 duck-typing 替代。
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class WorkerThread(QThread):
    """通用后台任务线程：在独立线程执行 `target(*args, on_progress=..., cancel_token=..., **kwargs)`。

    信号：
    - `progress(float)`：`target` 通过 `on_progress` 回调汇报的进度（0.0~1.0）。
    - `finished(object)`：`target` 正常返回时，携带其返回值。
    - `error(object)`：`target` 抛出异常时，携带该异常实例（含取消异常），供 GUI 层按类型
      翻译为中文提示（规则 B9），不在本模块内做任何异常类型判断或弹窗。
    """

    progress = pyqtSignal(float)
    finished = pyqtSignal(object)
    error = pyqtSignal(object)

    def __init__(
        self, target: Callable[..., Any], *args: Any, parent: Any = None, **kwargs: Any
    ) -> None:
        super().__init__(parent)
        self._target = target
        self._args = args
        self._kwargs = kwargs
        self.cancel_token = CancelToken()

    def cancel(self) -> None:
        """请求取消：设置取消令牌，`target` 需自行轮询 `cancel_token.is_cancelled()` 响应。"""
        self.cancel_token.cancel()

    def run(self) -> None:  # noqa: D102 — QThread 覆写方法，文档见类注释
        try:
            result = self._target(
                *self._args,
                on_progress=self.progress.emit,
                cancel_token=self.cancel_token,
                **self._kwargs,
            )
        except Exception as exc:  # noqa: BLE001 — 统一捕获后转发为 error 信号，不在此处翻译
            self.error.emit(exc)
            return
        self.finished.emit(result)
