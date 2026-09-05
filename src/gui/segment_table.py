"""分段脚本表格：增删行、编辑文本/起始时间，时间格式与重叠校验委托给 core 层。

依据 ARCHITECTURE.md 规则 B8：本模块只做表格编排与展示，不实现校验算法本身
（时间格式解析、重复起始时间检测等业务规则统一由 `src.core.script_parser.validate_segments`
提供，保证 GUI 与命令行/脚本导入路径共用同一套校验逻辑）。
"""
from __future__ import annotations

from typing import List, Optional

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
_COL_TEXT = 1
_COL_REMOVE = 2

_HEADERS = ["起始时间 (秒)", "文本", ""]


class SegmentTable(QTableWidget):
    """脚本分段表格。每行对应一个 `Segment`（不含 audio_path/duration，那些是运行期填充的
    合成结果，不属于用户编辑的原始输入）。
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(0, len(_HEADERS), parent)
        self.setHorizontalHeaderLabels(_HEADERS)
        header = self.horizontalHeader()
        header.setSectionResizeMode(_COL_TEXT, QHeaderView.ResizeMode.Stretch)

    def add_row(self, start_time: float = 0.0, text: str = "") -> None:
        row = self.rowCount()
        self.insertRow(row)

        start_spin = QDoubleSpinBox(self)
        start_spin.setRange(0.0, 24 * 3600.0)
        start_spin.setDecimals(2)
        start_spin.setValue(start_time)
        self.setCellWidget(row, _COL_START_TIME, start_spin)

        self.setItem(row, _COL_TEXT, QTableWidgetItem(text))

        remove_btn = QPushButton("删除", self)
        remove_btn.clicked.connect(lambda: self._remove_row_by_widget(remove_btn))
        self.setCellWidget(row, _COL_REMOVE, remove_btn)

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
            text_item = self.item(row, _COL_TEXT)
            text = text_item.text() if text_item is not None else ""
            segments.append(Segment(text=text, start_time=start_time))
        return segments

    def set_segments(self, segments: List[Segment]) -> None:
        self.setRowCount(0)
        for segment in segments:
            self.add_row(segment.start_time, segment.text)

    def validate(self) -> None:
        """委托 core 层校验当前表格内容；不合法时抛出 `ScriptParseError`（由调用方翻译为中文提示）。"""
        validate_segments(self.get_segments())
