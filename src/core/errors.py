"""领域异常的统一入口（F10）。

依据 ARCHITECTURE.md 规则 B9：core 层各模块抛出的领域异常均由职责相关的模块定义
（例如 `TTSNetworkError` 定义在 `tts_provider.py`、`VideoProcessorError` 及其子类定义在
`video_processor.py`），core 层自身绝不弹窗（不 import 任何 GUI 弹窗组件），弹窗提示统一
由 GUI 层翻译为中文文案。

本模块**不迁移**这些既有异常的定义位置（保持范围纪律，不改动已验证 feature 的现有代码），
只做两件事：
1. 统一 re-export 全部领域异常，供 GUI 等外部调用方从单一入口
   `from src.core.errors import ...` 引用，无需分别记住每个异常定义在哪个模块。
2. 承载 F10 新增、此前未被任何既有异常覆盖的场景（目前无需新增独立类型：
   「格式不支持」复用 `video_processor.UnsupportedVideoFormatError`，
   「TTS 网络失败与限流重试」复用既有 `TTSNetworkError`）。
"""
from __future__ import annotations

from src.core.audio_timeline import AudioTimelineError
from src.core.script_parser import ScriptParseError
from src.core.tts_provider import TTSNetworkError
from src.core.video_processor import (
    ExportCancelledError,
    ExportFailedError,
    NoAudioStreamError,
    UnsupportedVideoFormatError,
    VideoProcessorError,
)
from src.utils.ffmpeg_locator import FFmpegNotFoundError

__all__ = [
    "AudioTimelineError",
    "ScriptParseError",
    "TTSNetworkError",
    "VideoProcessorError",
    "NoAudioStreamError",
    "ExportCancelledError",
    "ExportFailedError",
    "UnsupportedVideoFormatError",
    "FFmpegNotFoundError",
]
