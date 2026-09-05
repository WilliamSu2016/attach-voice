"""tests/test_errors.py — src/core/errors.py 的单测（F10）。

覆盖两件事：
1. `errors.py` 统一 re-export 的每个名字都能正确导入，且与各自原始定义模块中的类是同一个
   对象（保证没有意外复制/遮蔽定义）。
2. F10 新增的 `UnsupportedVideoFormatError`：验证类型关系（是 `VideoProcessorError` 子类），
   并验证 `video_processor.probe()` 在「ffprobe 失败」「无视频流」「无法解析时长」三种
   格式不支持/文件损坏场景下均正确抛出该异常（而不是笼统的基类）。
"""
from __future__ import annotations

import json
import subprocess

import pytest

from src.core import errors
from src.core import video_processor as vp
from src.core.audio_timeline import AudioTimelineError
from src.core.script_parser import ScriptParseError
from src.core.tts_provider import TTSNetworkError
from src.core.video_processor import (
    ExportCancelledError,
    ExportFailedError,
    NoAudioStreamError,
    UnsupportedVideoFormatError,
    VideoProcessorError,
)
from src.utils.ffmpeg_locator import FFmpegNotFoundError


# ---------------------------------------------------------------------------
# 统一入口 re-export 正确性
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, original",
    [
        ("AudioTimelineError", AudioTimelineError),
        ("ScriptParseError", ScriptParseError),
        ("TTSNetworkError", TTSNetworkError),
        ("VideoProcessorError", VideoProcessorError),
        ("NoAudioStreamError", NoAudioStreamError),
        ("ExportCancelledError", ExportCancelledError),
        ("ExportFailedError", ExportFailedError),
        ("UnsupportedVideoFormatError", UnsupportedVideoFormatError),
        ("FFmpegNotFoundError", FFmpegNotFoundError),
    ],
)
def test_errors_module_reexports_are_identical_objects(name, original):
    assert getattr(errors, name) is original
    assert name in errors.__all__


def test_errors_all_matches_public_exports():
    assert set(errors.__all__) == {
        "AudioTimelineError",
        "ScriptParseError",
        "TTSNetworkError",
        "VideoProcessorError",
        "NoAudioStreamError",
        "ExportCancelledError",
        "ExportFailedError",
        "UnsupportedVideoFormatError",
        "FFmpegNotFoundError",
    }


# ---------------------------------------------------------------------------
# UnsupportedVideoFormatError：类型关系与实际抛出场景
# ---------------------------------------------------------------------------


def test_unsupported_video_format_error_is_video_processor_error_subclass():
    assert issubclass(UnsupportedVideoFormatError, VideoProcessorError)
    assert issubclass(UnsupportedVideoFormatError, RuntimeError)


def test_probe_raises_unsupported_format_on_ffprobe_nonzero_exit(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"invalid data found")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(UnsupportedVideoFormatError):
        vp.probe("corrupted.mp4")


def test_probe_raises_unsupported_format_when_no_video_stream(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    payload = {"format": {"duration": "1.0"}, "streams": [{"codec_type": "audio"}]}

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=json.dumps(payload).encode("utf-8"), stderr=b""
        )

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(UnsupportedVideoFormatError):
        vp.probe("audio_only.mp4")


def test_probe_raises_unsupported_format_when_duration_missing(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    payload = {
        "format": {},
        "streams": [{"codec_type": "video", "codec_name": "h264", "width": 100, "height": 100}],
    }

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=json.dumps(payload).encode("utf-8"), stderr=b""
        )

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(UnsupportedVideoFormatError):
        vp.probe("no_duration.mp4")
