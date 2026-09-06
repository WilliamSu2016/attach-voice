"""SRT 分段表格的字幕结束时间编辑回归测试。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication

from src.gui.segment_table import SegmentTable
from src.models import Segment


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_segment_table_round_trips_subtitle_end_time_with_millisecond_precision(app):
    table = SegmentTable()
    table.set_segments(
        [Segment(text="第一行\n第二行", start_time=1.234, subtitle_end_time=5.678)]
    )

    assert table.columnCount() == 5
    assert table.get_segments() == [
        Segment(text="第一行\n第二行", start_time=1.234, subtitle_end_time=5.678)
    ]


def test_segment_table_uses_none_for_unspecified_subtitle_end_time(app):
    table = SegmentTable()
    table.add_row(0.0, "手工脚本")

    assert table.get_segments()[0].subtitle_end_time is None
