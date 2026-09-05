"""TTSProvider 抽象基类：新增 TTS 引擎 = 新增一个文件，不得修改调用方。

依据 ARCHITECTURE.md#5 / PRODUCT.md D5：任何业务代码只能通过 TTSProvider 访问 TTS 能力，
不得直接 import edge_tts（规则 B10，唯一例外是 edge_tts_provider.py）。
本模块不 import PyQt6，符合规则 B1；不 import edge_tts，符合规则 B10。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    """TTS 音色信息（引擎无关的通用表示）。"""

    short_name: str        # 引擎内部使用的音色 ID，如 "zh-CN-XiaoxiaoNeural"
    locale: str             # 语言区域，如 "zh-CN"
    gender: str             # "Female" | "Male"
    friendly_name: str = ""  # 人类可读名称，用于 UI 展示


class TTSNetworkError(RuntimeError):
    """TTS 合成因网络问题最终失败（已按指数退避重试仍未成功）。"""


class TTSProvider(ABC):
    """TTS 引擎抽象基类。"""

    @abstractmethod
    def list_voices(self, language: str | None = None) -> list[Voice]:
        """列出可用音色，可按语言（如 "zh"、"en"）过滤。"""
        raise NotImplementedError

    @abstractmethod
    def synthesize(
        self, text: str, voice: str, rate: str, pitch: str, out_path: str
    ) -> float:
        """合成音频写入 out_path，返回实际时长（秒）。"""
        raise NotImplementedError
