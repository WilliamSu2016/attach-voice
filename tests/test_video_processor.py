"""tests/test_video_processor.py — src/core/video_processor.py 的单测。

分两部分：
1. 纯命令构造 / 进度解析断言（不依赖真实 ffmpeg，通过 monkeypatch 隔离 subprocess）。
2. 标记 `@pytest.mark.integration` 的端到端用例（需要真实 ffmpeg，会自动定位/下载；
   使用 ffmpeg `lavfi` 生成的极短合成视频，不依赖仓库外部样例文件）。
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from src.core import video_processor as vp
from src.core.audio_timeline import TimelineItem, TimelinePlan
from src.models import Project
from src.utils.ffmpeg_locator import FFmpegNotFoundError


# ---------------------------------------------------------------------------
# parse_progress_time
# ---------------------------------------------------------------------------


def test_parse_progress_time_basic():
    line = "frame=  120 fps=30 q=-1.0 size=    256kB time=00:00:04.00 bitrate= 524.3kbits/s"
    assert vp.parse_progress_time(line) == pytest.approx(4.0)


def test_parse_progress_time_with_hours():
    line = "time=01:02:03.50 bitrate=N/A"
    assert vp.parse_progress_time(line) == pytest.approx(3723.5)


def test_parse_progress_time_no_fraction():
    line = "time=00:00:10 bitrate=N/A"
    assert vp.parse_progress_time(line) == pytest.approx(10.0)


def test_parse_progress_time_returns_none_when_absent():
    assert vp.parse_progress_time("frame=1 fps=1 size=1kB") is None


# ---------------------------------------------------------------------------
# _ffmpeg_path / _ffprobe_path
# ---------------------------------------------------------------------------


def test_ffmpeg_path_raises_when_not_found(monkeypatch):
    monkeypatch.setattr(vp, "find_ffmpeg", lambda user_configured_path=None: None)
    with pytest.raises(FFmpegNotFoundError):
        vp._ffmpeg_path()


def test_ffmpeg_path_returns_located_path(monkeypatch):
    monkeypatch.setattr(vp, "find_ffmpeg", lambda user_configured_path=None: "C:/bin/ffmpeg.exe")
    assert vp._ffmpeg_path() == "C:/bin/ffmpeg.exe"


def test_ffprobe_path_derives_sibling_and_verifies(monkeypatch, tmp_path):
    ffmpeg_file = tmp_path / "ffmpeg.exe"
    ffmpeg_file.write_bytes(b"")
    ffprobe_file = tmp_path / "ffprobe.exe"
    ffprobe_file.write_bytes(b"")

    monkeypatch.setattr(vp, "verify_ffmpeg", lambda path: True)
    result = vp._ffprobe_path(str(ffmpeg_file))
    assert Path(result) == ffprobe_file


def test_ffprobe_path_raises_when_missing(tmp_path):
    ffmpeg_file = tmp_path / "ffmpeg.exe"
    ffmpeg_file.write_bytes(b"")
    # 未创建同目录的 ffprobe.exe
    with pytest.raises(FFmpegNotFoundError):
        vp._ffprobe_path(str(ffmpeg_file))


# ---------------------------------------------------------------------------
# probe()
# ---------------------------------------------------------------------------


def _fake_ffprobe_json(duration="12.5", width=1920, height=1080, has_audio=True):
    streams = [
        {"codec_type": "video", "codec_name": "h264", "width": width, "height": height}
    ]
    if has_audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    payload = {
        "format": {"duration": duration, "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": streams,
    }
    return json.dumps(payload).encode("utf-8")


def test_probe_parses_duration_resolution_audio_and_codec(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=_fake_ffprobe_json(), stderr=b"")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)

    info = vp.probe("video.mp4")
    assert info.duration == pytest.approx(12.5)
    assert info.width == 1920
    assert info.height == 1080
    assert info.has_audio is True
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"


def test_probe_no_audio_stream(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=_fake_ffprobe_json(has_audio=False), stderr=b""
        )

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    info = vp.probe("video.mp4")
    assert info.has_audio is False
    assert info.audio_codec is None


def test_probe_raises_on_ffprobe_failure(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(vp.VideoProcessorError):
        vp.probe("missing.mp4")


def test_probe_raises_when_no_video_stream(monkeypatch):
    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_ffprobe_path", lambda ffmpeg_path: "ffprobe.exe")

    payload = {"format": {"duration": "1.0"}, "streams": [{"codec_type": "audio"}]}

    def fake_run(cmd, stdout, stderr, timeout):
        return subprocess.CompletedProcess(
            cmd, returncode=0, stdout=json.dumps(payload).encode("utf-8"), stderr=b""
        )

    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    with pytest.raises(vp.VideoProcessorError):
        vp.probe("audio_only.mp4")


# ---------------------------------------------------------------------------
# build_narration_filter_complex
# ---------------------------------------------------------------------------


def _plan_with_segment_and_silence():
    items = [
        TimelineItem(kind="silence", start=0.0, end=1.0),
        TimelineItem(kind="segment", start=1.0, end=3.0, segment_index=0, audio_path="seg0.mp3"),
        TimelineItem(kind="silence", start=3.0, end=4.0),
    ]
    return TimelinePlan(items=items, total_duration=4.0, overflow=False)


def test_build_narration_filter_complex_orders_inputs_and_concats():
    plan = _plan_with_segment_and_silence()
    extra_input_args, filter_complex = vp.build_narration_filter_complex(
        plan, silence_input_offset=1
    )
    # 2 段静音 -> 2 组 -f lavfi -t <dur> -i anullsrc...
    assert extra_input_args.count("-f") == 2
    assert extra_input_args.count("lavfi") == 2
    assert "anullsrc=r=44100:cl=stereo" in extra_input_args[5]
    assert "[0:a]atrim=0:2.000000" in filter_complex
    assert "concat=n=3:v=0:a=1[narration]" in filter_complex
    # 静音下标从 silence_input_offset=1 开始
    assert "[1:a]anull[sil1]" in filter_complex
    assert "[2:a]anull[sil2]" in filter_complex


def test_build_narration_filter_complex_all_silence_single_item():
    items = [TimelineItem(kind="silence", start=0.0, end=5.0)]
    plan = TimelinePlan(items=items, total_duration=5.0, overflow=False)
    extra_input_args, filter_complex = vp.build_narration_filter_complex(plan, silence_input_offset=0)
    assert extra_input_args.count("-i") == 1
    assert "concat=n=1:v=0:a=1[narration]" in filter_complex


# ---------------------------------------------------------------------------
# _build_mux_command
# ---------------------------------------------------------------------------


def _video_info(has_audio=True):
    return vp.VideoInfo(
        duration=10.0, width=1920, height=1080, has_audio=has_audio,
        video_codec="h264", audio_codec="aac" if has_audio else None,
    )


def _project(mix_mode="replace", original_volume=0.2):
    return Project(video_path="in.mp4", mix_mode=mix_mode, original_volume=original_volume)


def test_build_mux_command_replace_mode_uses_copy_and_aac():
    cmd = vp._build_mux_command(
        "ffmpeg.exe", _project(mix_mode="replace"), _video_info(), "narration.wav", "out.mp4"
    )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-map" in cmd
    map_indices = [i for i, arg in enumerate(cmd) if arg == "-map"]
    mapped = [cmd[i + 1] for i in map_indices]
    assert mapped == ["0:v", "1:a"]
    assert "-filter_complex" not in cmd


def test_build_mux_command_overlay_mode_uses_amix_and_configurable_volume():
    cmd = vp._build_mux_command(
        "ffmpeg.exe",
        _project(mix_mode="overlay", original_volume=0.35),
        _video_info(has_audio=True),
        "narration.wav",
        "out.mp4",
    )
    assert "-filter_complex" in cmd
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "volume=0.35" in filter_complex
    assert "amix=inputs=2" in filter_complex
    assert "[aout]" in filter_complex
    map_indices = [i for i, arg in enumerate(cmd) if arg == "-map"]
    mapped = [cmd[i + 1] for i in map_indices]
    assert mapped == ["0:v", "[aout]"]


def test_build_mux_command_overlay_without_original_audio_falls_back_to_replace_mapping():
    cmd = vp._build_mux_command(
        "ffmpeg.exe",
        _project(mix_mode="overlay"),
        _video_info(has_audio=False),
        "narration.wav",
        "out.mp4",
    )
    assert "-filter_complex" not in cmd
    map_indices = [i for i, arg in enumerate(cmd) if arg == "-map"]
    mapped = [cmd[i + 1] for i in map_indices]
    assert mapped == ["0:v", "1:a"]


def test_build_mux_command_reencode_uses_libx264():
    cmd = vp._build_mux_command(
        "ffmpeg.exe", _project(), _video_info(), "narration.wav", "out.mp4", reencode_video=True
    )
    assert cmd[cmd.index("-c:v") + 1] == "libx264"


def test_build_mux_command_ffmpeg_path_not_hardcoded():
    cmd = vp._build_mux_command(
        "C:/custom/located/ffmpeg.exe", _project(), _video_info(), "narration.wav", "out.mp4"
    )
    assert cmd[0] == "C:/custom/located/ffmpeg.exe"


# ---------------------------------------------------------------------------
# _looks_like_codec_incompatibility
# ---------------------------------------------------------------------------


def test_looks_like_codec_incompatibility_true_on_known_pattern():
    assert vp._looks_like_codec_incompatibility(
        "Error: Codec not currently supported in container"
    )


def test_looks_like_codec_incompatibility_false_on_unrelated_error():
    assert not vp._looks_like_codec_incompatibility("No such file or directory")


# ---------------------------------------------------------------------------
# _run_ffmpeg_with_progress（用假 Popen 模拟真实子进程行为）
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self, lines, cancel_after=None):
        self._lines = list(lines)
        self._pos = 0
        self.terminated = False
        self.killed = False
        self._returncode = None
        self.stderr = self

    def __iter__(self):
        return self

    def __next__(self):
        if self._pos >= len(self._lines):
            raise StopIteration
        line = self._lines[self._pos]
        self._pos += 1
        return line

    def wait(self, timeout=None):
        self._returncode = 0
        return 0

    def terminate(self):
        self.terminated = True
        self._returncode = -15

    def kill(self):
        self.killed = True
        self._returncode = -9

    def poll(self):
        return self._returncode


def test_run_ffmpeg_with_progress_reports_progress(monkeypatch):
    lines = [
        "frame=1 time=00:00:01.00 bitrate=1kb/s\n",
        "frame=2 time=00:00:05.00 bitrate=1kb/s\n",
    ]
    fake = _FakeProcess(lines)
    monkeypatch.setattr(vp.subprocess, "Popen", lambda *a, **kw: fake)

    progresses = []
    stderr_text = vp._run_ffmpeg_with_progress(
        ["ffmpeg.exe"], total_duration=10.0, on_progress=progresses.append,
        cancel_token=None, cleanup_paths=[],
    )
    assert progresses == [pytest.approx(0.1), pytest.approx(0.5)]
    assert "time=00:00:05.00" in stderr_text


def test_run_ffmpeg_with_progress_cancels_and_cleans_up(monkeypatch, tmp_path):
    leftover = tmp_path / "narration.wav"
    leftover.write_bytes(b"data")

    lines = [
        "frame=1 time=00:00:01.00 bitrate=1kb/s\n",
        "frame=2 time=00:00:02.00 bitrate=1kb/s\n",
        "frame=3 time=00:00:03.00 bitrate=1kb/s\n",
    ]
    fake = _FakeProcess(lines)
    monkeypatch.setattr(vp.subprocess, "Popen", lambda *a, **kw: fake)

    class _AlwaysCancelled:
        def is_cancelled(self):
            return True

    with pytest.raises(vp.ExportCancelledError):
        vp._run_ffmpeg_with_progress(
            ["ffmpeg.exe"], total_duration=10.0, on_progress=None,
            cancel_token=_AlwaysCancelled(), cleanup_paths=[str(leftover)],
        )
    assert fake.terminated is True
    assert not leftover.exists()


# ---------------------------------------------------------------------------
# export_audio_only（PRODUCT R10「仅导出音频」，F07 GUI 按钮对应能力）
# ---------------------------------------------------------------------------


def test_export_audio_only_mp3_uses_libmp3lame_codec(monkeypatch, tmp_path):
    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-audio")
        return "time=00:00:01.00 "

    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_run_ffmpeg_with_progress", fake_run)

    item = TimelineItem(kind="segment", start=0.0, end=1.0, segment_index=0, audio_path="seg0.wav")
    plan = TimelinePlan(items=[item], total_duration=1.0, overflow=False)
    out_path = str(tmp_path / "narration.mp3")

    vp.export_audio_only(plan, out_path)

    assert "-c:a" in captured_cmd["cmd"]
    assert captured_cmd["cmd"][captured_cmd["cmd"].index("-c:a") + 1] == "libmp3lame"
    assert Path(out_path).is_file()


def test_export_audio_only_wav_uses_pcm_codec(monkeypatch, tmp_path):
    captured_cmd = {}

    def fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-audio")
        return "time=00:00:01.00 "

    monkeypatch.setattr(vp, "_ffmpeg_path", lambda user_configured_path=None: "ffmpeg.exe")
    monkeypatch.setattr(vp, "_run_ffmpeg_with_progress", fake_run)

    item = TimelineItem(kind="segment", start=0.0, end=1.0, segment_index=0, audio_path="seg0.wav")
    plan = TimelinePlan(items=[item], total_duration=1.0, overflow=False)
    out_path = str(tmp_path / "narration.wav")

    vp.export_audio_only(plan, out_path)

    assert captured_cmd["cmd"][captured_cmd["cmd"].index("-c:a") + 1] == "pcm_s16le"


def test_export_audio_only_raises_if_already_cancelled(tmp_path):
    class _AlwaysCancelled:
        def is_cancelled(self):
            return True

    item = TimelineItem(kind="segment", start=0.0, end=1.0, segment_index=0, audio_path="seg0.wav")
    plan = TimelinePlan(items=[item], total_duration=1.0, overflow=False)

    with pytest.raises(vp.ExportCancelledError):
        vp.export_audio_only(
            plan, str(tmp_path / "out.mp3"), cancel_token=_AlwaysCancelled()
        )


# ---------------------------------------------------------------------------
# _cleanup
# ---------------------------------------------------------------------------


def test_cleanup_removes_existing_files(tmp_path):
    f1 = tmp_path / "a.tmp"
    f2 = tmp_path / "b.tmp"
    f1.write_bytes(b"x")
    f2.write_bytes(b"y")
    vp._cleanup([str(f1), str(f2), str(tmp_path / "missing.tmp")])
    assert not f1.exists()
    assert not f2.exists()


# ---------------------------------------------------------------------------
# 集成测试：真实 ffmpeg + 合成短视频（不依赖仓库外部样例文件）
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_ffmpeg_path():
    from src.utils.ffmpeg_locator import download_ffmpeg, find_ffmpeg

    path = find_ffmpeg()
    if not path:
        path = download_ffmpeg()
    return path


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory, real_ffmpeg_path):
    """用 ffmpeg lavfi 生成一个 6 秒、320x240、含音轨的合成 mp4，供集成测试使用。"""
    out_dir = tmp_path_factory.mktemp("sample_video")
    out_path = out_dir / "sample.mp4"
    cmd = [
        real_ffmpeg_path, "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out_path),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return str(out_path)


@pytest.mark.integration
def test_probe_real_sample_video(sample_video):
    info = vp.probe(sample_video)
    assert info.duration == pytest.approx(6.0, abs=0.2)
    assert info.width == 320
    assert info.height == 240
    assert info.has_audio is True
    assert info.video_codec in ("h264",)


@pytest.mark.integration
def test_export_replace_mode_real_ffmpeg(tmp_path, sample_video, real_ffmpeg_path):
    from src.core.edge_tts_provider import EdgeTTSProvider

    provider = EdgeTTSProvider()
    seg_audio_path = str(tmp_path / "seg0.mp3")
    duration = provider.synthesize(
        "你好，这是一段测试配音。", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", seg_audio_path
    )

    from src.models import Segment
    from src.core.audio_timeline import build_timeline

    segments = [Segment(text="test", start_time=1.0, audio_path=seg_audio_path, duration=duration)]
    plan = build_timeline(segments, video_duration=6.0)

    project = Project(video_path=sample_video, mix_mode="replace")
    out_path = str(tmp_path / "out.mp4")

    progress_values = []
    vp.export(project, plan, out_path, on_progress=progress_values.append)

    assert Path(out_path).is_file()
    out_info = vp.probe(out_path)
    # D2：画面时长与源一致
    assert out_info.duration == pytest.approx(6.0, abs=0.05)
    assert out_info.has_audio is True
    assert progress_values, "应至少回报过一次进度"


@pytest.mark.integration
def test_export_audio_only_produces_playable_mp3_real_ffmpeg(tmp_path):
    from src.core.edge_tts_provider import EdgeTTSProvider
    from src.core.audio_timeline import build_timeline
    from src.models import Segment

    provider = EdgeTTSProvider()
    seg_audio_path = str(tmp_path / "seg0.mp3")
    duration = provider.synthesize(
        "你好，这是仅导出音频的测试。", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", seg_audio_path
    )

    segments = [Segment(text="test", start_time=0.5, audio_path=seg_audio_path, duration=duration)]
    plan = build_timeline(segments, video_duration=duration + 1.0)

    out_path = str(tmp_path / "narration_only.mp3")
    progress_values = []
    vp.export_audio_only(plan, out_path, on_progress=progress_values.append)

    assert Path(out_path).is_file()
    assert Path(out_path).stat().st_size > 0
    # ffprobe 校验产物是可探测的音频（时长应约等于 plan.total_duration）
    ffmpeg_path = vp._ffmpeg_path()
    ffprobe_path = vp._ffprobe_path(ffmpeg_path)
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", out_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    assert result.returncode == 0
    reported_duration = float(json.loads(result.stdout)["format"]["duration"])
    assert reported_duration == pytest.approx(plan.total_duration, abs=0.3)
    assert progress_values, "应至少回报过一次进度"


@pytest.mark.integration
def test_export_cancel_terminates_process_and_cleans_up(tmp_path, sample_video):
    from src.core.audio_timeline import build_timeline
    from src.models import Segment

    segments = [Segment(text="t", start_time=0.5, audio_path=None, duration=1.0)]
    # 不提供真实 audio_path 会在渲染配音轨阶段失败——改为直接测试取消发生在渲染之前的路径：
    # 用一个全静音的 plan，确保 _render_narration_track 能顺利完成，再在混音阶段取消。
    plan = build_timeline([], video_duration=6.0)

    project = Project(video_path=sample_video, mix_mode="replace")
    out_path = str(tmp_path / "out_cancel.mp4")

    token = vp.CancelToken()

    def cancel_soon():
        time.sleep(0.05)
        token.cancel()

    import threading

    t = threading.Thread(target=cancel_soon)
    t.start()
    with pytest.raises(vp.ExportCancelledError):
        vp.export(project, plan, out_path, cancel_token=token)
    t.join()

    assert not Path(out_path).exists()
