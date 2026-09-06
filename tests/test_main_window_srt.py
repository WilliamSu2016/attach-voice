"""主界面 SRT 导入、导出与配音失效规则。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.gui.main_window import MainWindow, _generate_narration
from src.models import Segment
from src.utils.async_worker import CancelToken
from src.utils.settings import AppSettings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    return MainWindow(settings=settings)


def test_import_srt_replaces_table_and_clears_stale_audio(window, tmp_path, monkeypatch):
    source = tmp_path / "input.srt"
    source.write_text(
        "1\n00:00:01,234 --> 00:00:03,456\n第一行\n第二行\n", encoding="utf-8-sig"
    )
    window._segment_table.add_row(0.0, "旧脚本")
    window._synthesized_segments = [Segment("旧脚本", 0.0, audio_path="old.mp3", duration=1.0)]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args: (str(source), "")))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *args: QMessageBox.StandardButton.Yes)
    )

    window._on_import_srt_clicked()

    assert window._segment_table.get_segments() == [
        Segment(text="第一行\n第二行", start_time=1.234, subtitle_end_time=3.456)
    ]
    assert window._synthesized_segments == []


def test_failed_import_leaves_existing_table_and_audio_unchanged(window, tmp_path, monkeypatch):
    source = tmp_path / "invalid.srt"
    source.write_text("1\nnot a timestamp\n文本\n", encoding="utf-8")
    window._segment_table.add_row(2.0, "保留内容")
    old_audio = [Segment("保留内容", 2.0, audio_path="old.mp3", duration=1.0)]
    window._synthesized_segments = old_audio
    errors = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args: (str(source), "")))
    monkeypatch.setattr(window, "_show_error", lambda title, message: errors.append((title, message)))

    window._on_import_srt_clicked()

    assert window._segment_table.get_segments()[0].text == "保留内容"
    assert window._synthesized_segments is old_audio
    assert errors and errors[0][0] == "导入 SRT 失败"


def test_declined_import_leaves_existing_table_and_audio_unchanged(window, tmp_path, monkeypatch):
    source = tmp_path / "input.srt"
    source.write_text("1\n00:00:01,000 --> 00:00:02,000\n新内容\n", encoding="utf-8")
    window._segment_table.add_row(2.0, "保留内容")
    old_audio = [Segment("保留内容", 2.0, audio_path="old.mp3", duration=1.0)]
    window._synthesized_segments = old_audio
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *args: (str(source), "")))
    monkeypatch.setattr(
        QMessageBox, "question", staticmethod(lambda *args: QMessageBox.StandardButton.No)
    )

    window._on_import_srt_clicked()

    assert window._segment_table.get_segments()[0].text == "保留内容"
    assert window._synthesized_segments is old_audio


def test_export_srt_writes_edited_subtitle_timing(window, tmp_path, monkeypatch):
    target = tmp_path / "edited.srt"
    window._segment_table.set_segments(
        [Segment("字幕", 1.0, duration=9.0, subtitle_end_time=2.345)]
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *args: (str(target), "")))
    monkeypatch.setattr(window, "_show_info", lambda *args: None)

    window._on_export_srt_clicked()

    assert target.read_text(encoding="utf-8") == "1\n00:00:01,000 --> 00:00:02,345\n字幕\n\n"


def test_text_or_start_edit_invalidates_audio_but_end_edit_does_not(window):
    window._synthesized_segments = [Segment("文本", 0.0, audio_path="audio.mp3", duration=1.0)]

    window._on_segments_edited(False)
    assert window._synthesized_segments
    window._on_segments_edited(True)
    assert window._synthesized_segments == []


def test_regenerated_audio_preserves_subtitle_end_time(tmp_path):
    class FakeTTS:
        def synthesize(self, text, voice, rate, pitch, out_path):
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            Path(out_path).write_bytes(b"audio")
            return 4.0

    result = _generate_narration(
        FakeTTS(),
        [Segment("字幕", 1.0, subtitle_end_time=2.0)],
        "voice",
        "+0%",
        "+0Hz",
        str(tmp_path),
        lambda _value: None,
        CancelToken(),
    )

    assert result[0].duration == 4.0
    assert result[0].subtitle_end_time == 2.0
