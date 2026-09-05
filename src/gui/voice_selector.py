"""音色选择控件：语言过滤 + 音色下拉 + 语速/音调调节。

依据 ARCHITECTURE.md 规则 B8：本模块只做 UI 编排与展示，不含业务算法；
音色列表通过 `TTSProvider` 抽象获取（规则 D5/B10：不得直接 import edge_tts）。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QSpinBox,
    QWidget,
)

from src.core.tts_provider import TTSProvider, Voice

# 语言过滤下拉的可选项：显示名 -> 传给 TTSProvider.list_voices 的过滤字符串（None 表示不过滤）
_LANGUAGE_FILTERS: List[tuple[str, Optional[str]]] = [
    ("全部语言", None),
    ("中文", "zh"),
    ("英文", "en"),
    ("日文", "ja"),
]


class VoiceSelector(QWidget):
    """音色选择控件。构造时不主动拉取音色列表，需显式调用 `load_voices()`
    （避免在窗口初始化阶段就发起网络请求，阻塞界面绘制）。
    """

    def __init__(self, tts_provider: TTSProvider, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._tts_provider = tts_provider
        self._all_voices: List[Voice] = []

        self._language_combo = QComboBox(self)
        for label, _ in _LANGUAGE_FILTERS:
            self._language_combo.addItem(label)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)

        self._voice_combo = QComboBox(self)

        # edge-tts 的 rate/pitch 以 "+N%"/"+NHz" 表示；用 QSpinBox 取整数值，
        # 展示层直接拼接符号即可，无需在 GUI 层做额外的格式校验算法。
        self._rate_spin = QSpinBox(self)
        self._rate_spin.setRange(-50, 100)
        self._rate_spin.setSuffix("%")
        self._rate_spin.setValue(0)

        self._pitch_spin = QSpinBox(self)
        self._pitch_spin.setRange(-50, 50)
        self._pitch_spin.setSuffix("Hz")
        self._pitch_spin.setValue(0)

        layout = QFormLayout(self)
        layout.addRow("语言", self._language_combo)
        layout.addRow("音色", self._voice_combo)
        layout.addRow("语速", self._rate_spin)
        layout.addRow("音调", self._pitch_spin)

    def load_voices(self) -> None:
        """从 TTSProvider 拉取全部音色并填充下拉框（按当前语言过滤显示）。"""
        self._all_voices = self._tts_provider.list_voices()
        self._populate_voice_combo()

    def _on_language_changed(self, _index: int) -> None:
        self._populate_voice_combo()

    def _populate_voice_combo(self) -> None:
        _, lang_filter = _LANGUAGE_FILTERS[self._language_combo.currentIndex()]
        self._voice_combo.clear()
        for voice in self._all_voices:
            if lang_filter is not None and lang_filter.lower() not in voice.locale.lower():
                continue
            display = voice.friendly_name or voice.short_name
            self._voice_combo.addItem(display, voice.short_name)

    def selected_voice_short_name(self) -> Optional[str]:
        return self._voice_combo.currentData()

    def select_voice_by_short_name(self, short_name: Optional[str]) -> None:
        """按 `short_name` 选中音色（用于从持久化配置恢复）。为保证能找到目标音色，
        先将语言过滤切回"全部语言"，再在完整列表中定位。找不到则保持当前选择不变。
        """
        if not short_name:
            return
        if not any(voice.short_name == short_name for voice in self._all_voices):
            return
        self._language_combo.setCurrentIndex(0)  # 全部语言
        self._populate_voice_combo()
        index = self._voice_combo.findData(short_name)
        if index >= 0:
            self._voice_combo.setCurrentIndex(index)

    def set_rate(self, value: int) -> None:
        self._rate_spin.setValue(value)

    def set_pitch(self, value: int) -> None:
        self._pitch_spin.setValue(value)

    def selected_rate(self) -> str:
        value = self._rate_spin.value()
        return f"+{value}%" if value >= 0 else f"{value}%"

    def selected_pitch(self) -> str:
        value = self._pitch_spin.value()
        return f"+{value}Hz" if value >= 0 else f"{value}Hz"
