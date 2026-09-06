"""F15 end-to-end verification for MP4 mov_text embedded subtitles."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from src.core import video_processor as vp
from src.core.audio_timeline import TimelineItem, TimelinePlan
from src.gui.main_window import MainWindow
from src.models import Project, Segment
from src.utils.settings import AppSettings


def _run(command: list[str]) -> None:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _streams(ffprobe_path: str, path: str) -> dict:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    assert result.returncode == 0
    return json.loads(result.stdout)


def main() -> int:
    app = QApplication.instance() or QApplication([])
    ffmpeg_path = vp._ffmpeg_path()
    ffprobe_path = vp._ffprobe_path(ffmpeg_path)
    with tempfile.TemporaryDirectory(prefix="attach-voice-embedded-subtitles-") as root:
        root_path = Path(root)
        source = root_path / "source.mp4"
        no_audio = root_path / "no-audio.mp4"
        subtitles_only = root_path / "subtitles-only.mp4"
        subtitles_no_audio = root_path / "subtitles-no-audio.mp4"
        narration_subtitles = root_path / "narration-subtitles.mp4"
        _run([
            ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
        ])
        _run([
            ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(no_audio),
        ])
        source_duration = float(_streams(ffprobe_path, str(source))["format"]["duration"])
        no_audio_duration = float(_streams(ffprobe_path, str(no_audio))["format"]["duration"])
        captions = [Segment("第一行\n第二行", 1.234, subtitle_end_time=3.456)]
        vp.export_subtitles(str(source), captions, str(subtitles_only))
        vp.export_subtitles(str(no_audio), captions, str(subtitles_no_audio))
        silence_plan = TimelinePlan([TimelineItem("silence", 0.0, 6.0)], 6.0, False)
        vp.export(
            Project(video_path=str(source), mix_mode="replace"), silence_plan,
            str(narration_subtitles), subtitles=captions,
        )

        for path, has_audio, expected_duration in (
            (subtitles_only, True, source_duration),
            (subtitles_no_audio, False, no_audio_duration),
            (narration_subtitles, True, source_duration),
        ):
            data = _streams(ffprobe_path, str(path))
            subtitle = next(s for s in data["streams"] if s["codec_type"] == "subtitle")
            assert subtitle["codec_name"] == "mov_text"
            assert subtitle["disposition"]["default"] == 1
            assert any(s["codec_type"] == "audio" for s in data["streams"]) is has_audio
            actual_duration = float(data["format"]["duration"])
            assert abs(actual_duration - expected_duration) < 0.05, (
                f"{path.name}: expected={expected_duration}, actual={actual_duration}"
            )

        extracted = root_path / "extracted.srt"
        _run([ffmpeg_path, "-y", "-i", str(subtitles_only), "-map", "0:s:0", str(extracted)])
        text = extracted.read_text(encoding="utf-8")
        assert "00:00:01,234 --> 00:00:03,456" in text and "第一行\n第二行" in text

        window = MainWindow(settings=AppSettings(QSettings(str(root_path / "settings.ini"), QSettings.Format.IniFormat)))
        assert not window._embed_subtitles_checkbox.isChecked()
        window.deleteLater()
        app.processEvents()
    print("PASS — mov_text subtitle track, default disposition, audio preservation, timing, and GUI default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
