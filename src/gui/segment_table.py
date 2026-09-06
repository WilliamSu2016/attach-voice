"""分段脚本表格：增删行、编辑文本/起始时间，时间格式与重叠校验委托给 core 层。

依据 ARCHITECTURE.md 规则 B8：本模块只做表格编排与展示，不实现校验算法本身
（时间格式解析、重复起始时间检测等业务规则统一由 `src.core.script_parser.validate_segments`
提供，保证 GUI 与命令行/脚本导入路径共用同一套校验逻辑）。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDoubleSpinBox,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from src.core.script_parser import validate_segments
from src.models import Segment

_COL_START_TIME = 0
_COL_END_TIME = 1
_COL_TEXT = 2
_COL_PREVIEW = 3
_COL_REMOVE = 4

_HEADERS = ["起始时间 (秒)", "字幕结束时间 (秒)", "文本", "", ""]


class SegmentTable(QTableWidget):
    """脚本分段表格。每行对应一个 `Segment`（不含 audio_path/duration，那些是运行期填充的
    合成结果，不属于用户编辑的原始输入）。

    每行提供一个「试听」按钮（删除按钮左侧），点击时发出 `preview_row_requested(row)` 信号，
    由 `MainWindow` 负责查该行对应的已合成音频并播放——本类不持有音频路径/播放器，遵守
    ARCHITECTURE.md 规则 B8（GUI 表格只做编排展示，不碰业务数据/播放逻辑）。
    """

    preview_row_requested = pyqtSignal(int)
    segments_edited = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, len(_HEADERS), parent)
        self.setHorizontalHeaderLabels(_HEADERS)
        header = self.horizontalHeader()
        header.setSectionResizeMode(_COL_TEXT, QHeaderView.ResizeMode.Stretch)
        self.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == _COL_TEXT:
            self.segments_edited.emit(True)

    def add_row(
        self, start_time: float = 0.0, text: str = "", subtitle_end_time: float | None = None
    ) -> None:
        row = self.rowCount()
        self.insertRow(row)

        start_spin = QDoubleSpinBox(self)
        start_spin.setRange(0.0, 24 * 3600.0)
        start_spin.setDecimals(3)
        start_spin.setValue(start_time)
        start_spin.valueChanged.connect(lambda _value: self.segments_edited.emit(True))
        self.setCellWidget(row, _COL_START_TIME, start_spin)

        end_spin = QDoubleSpinBox(self)
        end_spin.setRange(-1.0, 24 * 3600.0)
        end_spin.setDecimals(3)
        end_spin.setSpecialValueText("未设置")
        end_spin.setValue(-1.0 if subtitle_end_time is None else subtitle_end_time)
        end_spin.valueChanged.connect(lambda _value: self.segments_edited.emit(False))
        self.setCellWidget(row, _COL_END_TIME, end_spin)

        self.setItem(row, _COL_TEXT, QTableWidgetItem(text))

        preview_btn = QPushButton("试听", self)
        preview_btn.clicked.connect(lambda: self._emit_preview_by_widget(preview_btn))
        self.setCellWidget(row, _COL_PREVIEW, preview_btn)

        remove_btn = QPushButton("删除", self)
        remove_btn.clicked.connect(lambda: self._remove_row_by_widget(remove_btn))
        self.setCellWidget(row, _COL_REMOVE, remove_btn)

    def _emit_preview_by_widget(self, button: QPushButton) -> None:
        for row in range(self.rowCount()):
            if self.cellWidget(row, _COL_PREVIEW) is button:
                self.preview_row_requested.emit(row)
                return

    def _remove_row_by_widget(self, button: QPushButton) -> None:
        for row in range(self.rowCount()):
            if self.cellWidget(row, _COL_REMOVE) is button:
                self.removeRow(row)
                return

    def remove_row(self, row: int) -> None:
        self.removeRow(row)

    def get_segments(self) -> List[Segment]:
        segments: List[Segment] = []
        for row in range(self.rowCount()):
            start_widget = self.cellWidget(row, _COL_START_TIME)
            start_time = start_widget.value() if start_widget is not None else 0.0
            end_widget = self.cellWidget(row, _COL_END_TIME)
            subtitle_end_time = (
                end_widget.value() if end_widget is not None and end_widget.value() >= 0.0 else None
            )
            text_item = self.item(row, _COL_TEXT)
            text = text_item.text() if text_item is not None else ""
            segments.append(
                Segment(
                    text=text,
                    start_time=start_time,
                    subtitle_end_time=subtitle_end_time,
                )
            )
        return segments

    def set_segments(self, segments: List[Segment]) -> None:
        self.blockSignals(True)
        try:
            self.setRowCount(0)
            for segment in segments:
                self.add_row(segment.start_time, segment.text, segment.subtitle_end_time)
        finally:
            self.blockSignals(False)

    def validate(self) -> None:
        """委托 core 层校验当前表格内容；不合法时抛出 `ScriptParseError`（由调用方翻译为中文提示）。"""
        validate_segments(self.get_segments())
