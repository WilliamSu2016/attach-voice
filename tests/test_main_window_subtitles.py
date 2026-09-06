"""F15 GUI selection and export routing tests."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QFileDialog

from src.core.video_processor import VideoInfo
from src.gui.main_window import MainWindow
from src.models import Segment
from src.utils.settings import AppSettings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path):
    settings = AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    view = MainWindow(settings=settings)
    view._video_path = str(tmp_path / "video.mp4")
    view._video_info = VideoInfo(6.0, 320, 240, True, "h264", "aac")
    view._segment_table.set_segments([Segment("字幕", 1.0, subtitle_end_time=2.0)])
    return view


def test_embedded_subtitles_default_off(window):
    assert not window._embed_subtitles_checkbox.isChecked()


def test_subtitles_only_uses_dedicated_worker_without_tts(window, tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *args: (str(tmp_path / "out.mp4"), ""))
    )
    monkeypatch.setattr("src.gui.main_window.WorkerThread", lambda target, *args, **kwargs: (target, args))
    monkeypatch.setattr(
        window, "_start_worker", lambda worker, callback: captured.update(worker=worker)
    )
    window._embed_subtitles_checkbox.setChecked(True)
    window._on_export_clicked()

    assert captured["worker"][0].__name__ == "_export_subtitles_task"
    assert captured["worker"][1][1][0].subtitle_end_time == 2.0
