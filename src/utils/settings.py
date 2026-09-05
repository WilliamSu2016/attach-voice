"""配置持久化：本模块是仓库中唯一允许使用 `QSettings` 的模块（ARCHITECTURE.md 规则 B7）。

依据 PRODUCT.md R11，持久化以下字段：音色、语速、音调、输出目录、混音模式、原声音量。

设计要点：
- `AppSettings` 包装一个 `QSettings` 实例，对外只暴露强类型的 getter/setter，
  调用方（`gui/main_window.py`）不需要也不应该知道底层是 `QSettings`。
- 每个 getter 对"键缺失"或"值类型损坏"（例如注册表项被手工改成非法值）都做了防御性处理，
  返回合理默认值而不抛异常，满足"首次运行使用合理默认值，不因缺失键崩溃"的验收标准。
- 默认使用真实的 `QSettings(organization, application)`（Windows 上落地到注册表
  `HKEY_CURRENT_USER\\Software\\<organization>\\<application>`）；构造函数也接受注入一个
  已构造好的 `QSettings` 实例（例如指向临时 ini 文件），便于测试/验证时不污染开发者本机的
  真实注册表项，同时仍然走真实的 `QSettings` 读写路径（非 mock）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSettings

ORGANIZATION = "attach-voice"
APPLICATION = "attach-voice"

_DEFAULT_VOICE: Optional[str] = None
_DEFAULT_RATE = 0
_DEFAULT_PITCH = 0
_DEFAULT_OUTPUT_DIR = ""
_DEFAULT_MIX_MODE = "replace"
_DEFAULT_ORIGINAL_VOLUME = 0.2
_VALID_MIX_MODES = ("replace", "overlay")

_KEY_VOICE = "tts/voice"
_KEY_RATE = "tts/rate"
_KEY_PITCH = "tts/pitch"
_KEY_OUTPUT_DIR = "export/output_dir"
_KEY_MIX_MODE = "mix/mode"
_KEY_ORIGINAL_VOLUME = "mix/original_volume"


class AppSettings:
    """`QSettings` 的强类型包装，供 GUI 层读写持久化配置。"""

    def __init__(self, backing: Optional[QSettings] = None) -> None:
        self._settings = backing if backing is not None else QSettings(ORGANIZATION, APPLICATION)

    # ------------------------------------------------------------------
    # 音色
    # ------------------------------------------------------------------
    def voice(self) -> Optional[str]:
        value = self._settings.value(_KEY_VOICE, _DEFAULT_VOICE)
        if not value:
            return _DEFAULT_VOICE
        return str(value)

    def set_voice(self, short_name: Optional[str]) -> None:
        self._settings.setValue(_KEY_VOICE, short_name or "")

    # ------------------------------------------------------------------
    # 语速 / 音调（整数，单位 %/Hz，与 VoiceSelector 的 QSpinBox 范围一致）
    # ------------------------------------------------------------------
    def rate(self) -> int:
        return self._read_int(_KEY_RATE, _DEFAULT_RATE)

    def set_rate(self, value: int) -> None:
        self._settings.setValue(_KEY_RATE, int(value))

    def pitch(self) -> int:
        return self._read_int(_KEY_PITCH, _DEFAULT_PITCH)

    def set_pitch(self, value: int) -> None:
        self._settings.setValue(_KEY_PITCH, int(value))

    # ------------------------------------------------------------------
    # 输出目录（导出/仅导出音频对话框的默认起始目录）
    # ------------------------------------------------------------------
    def output_dir(self) -> str:
        value = self._settings.value(_KEY_OUTPUT_DIR, _DEFAULT_OUTPUT_DIR)
        return str(value) if value else _DEFAULT_OUTPUT_DIR

    def set_output_dir(self, path: str) -> None:
        self._settings.setValue(_KEY_OUTPUT_DIR, path or "")

    # ------------------------------------------------------------------
    # 混音模式
    # ------------------------------------------------------------------
    def mix_mode(self) -> str:
        value = self._settings.value(_KEY_MIX_MODE, _DEFAULT_MIX_MODE)
        value = str(value) if value else _DEFAULT_MIX_MODE
        return value if value in _VALID_MIX_MODES else _DEFAULT_MIX_MODE

    def set_mix_mode(self, mode: str) -> None:
        self._settings.setValue(_KEY_MIX_MODE, mode if mode in _VALID_MIX_MODES else _DEFAULT_MIX_MODE)

    # ------------------------------------------------------------------
    # 原声音量（overlay 模式下的滑块值，范围 0.0–1.0）
    # ------------------------------------------------------------------
    def original_volume(self) -> float:
        raw = self._settings.value(_KEY_ORIGINAL_VOLUME, _DEFAULT_ORIGINAL_VOLUME)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return _DEFAULT_ORIGINAL_VOLUME
        if value < 0.0 or value > 1.0:
            return _DEFAULT_ORIGINAL_VOLUME
        return value

    def set_original_volume(self, value: float) -> None:
        self._settings.setValue(_KEY_ORIGINAL_VOLUME, float(value))

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _read_int(self, key: str, default: int) -> int:
        raw = self._settings.value(key, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def sync(self) -> None:
        """强制落盘（正常情况下 QSettings 会在合适的时机自动写入，仅在需要立即持久化的场景显式调用）。"""
        self._settings.sync()
