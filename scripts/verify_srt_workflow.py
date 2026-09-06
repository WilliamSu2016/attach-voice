"""F14 SRT workflow verification using a real offscreen MainWindow."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from src.gui.main_window import MainWindow, _generate_narration
from src.models import Segment
from src.utils.async_worker import CancelToken
from src.utils.settings import AppSettings


class _FakeTTSProvider:
    def synthesize(self, text, voice, rate, pitch, out_path):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"audio")
        return 4.0


def main() -> int:
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(prefix="attach-voice-srt-") as temp_dir:
        root = Path(temp_dir)
        source = root / "input.srt"
        output = root / "edited.srt"
        source.write_text(
            "1\n00:00:01,234 --> 00:00:03,456\n第一行\n第二行\n",
            encoding="utf-8-sig",
        )
        settings = AppSettings(
            QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        )
        window = MainWindow(settings=settings)
        window._segment_table.add_row(0.0, "旧脚本")

        original_open = QFileDialog.getOpenFileName
        original_save = QFileDialog.getSaveFileName
        original_question = QMessageBox.question
        try:
            QFileDialog.getOpenFileName = staticmethod(lambda *args: (str(source), ""))
            QFileDialog.getSaveFileName = staticmethod(lambda *args: (str(output), ""))
            QMessageBox.question = staticmethod(
                lambda *args: QMessageBox.StandardButton.Yes
            )
            window._show_info = lambda *args: None
            window._on_import_srt_clicked()
            imported = window._segment_table.get_segments()
            assert imported == [
                Segment("第一行\n第二行", 1.234, subtitle_end_time=3.456)
            ]

            regenerated = _generate_narration(
                _FakeTTSProvider(),
                imported,
                "voice",
                "+0%",
                "+0Hz",
                str(root),
                lambda _value: None,
                CancelToken(),
            )
            assert regenerated[0].duration == 4.0
            assert regenerated[0].subtitle_end_time == 3.456

            window._segment_table.set_segments(regenerated)
            window._on_export_srt_clicked()
            assert output.read_text(encoding="utf-8") == (
                "1\n00:00:01,234 --> 00:00:03,456\n第一行\n第二行\n\n"
            )
        finally:
            QFileDialog.getOpenFileName = original_open
            QFileDialog.getSaveFileName = original_save
            QMessageBox.question = original_question
            window.deleteLater()
            app.processEvents()

    print("PASS — SRT import, edit/export, millisecond timing, and TTS metadata separation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
