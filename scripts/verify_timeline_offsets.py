#!/usr/bin/env python
"""scripts/verify_timeline_offsets.py

F05 验证条目 3：渲染真实音轨后，各段能量起点与设定 start_time 偏差 < 0.2s（PRODUCT A2）。

流程：
1. 用 EdgeTTSProvider 真实合成 2 段有声文本（真实联网调用微软语音服务）。
2. 用 build_timeline() 计算布局（纯函数，不生成音频）。
3. 按布局把「静音」渲染为 PCM 静音帧、把「段落」的 mp3 用 ffmpeg 解码为 PCM，
   依次拼接成一条完整的 wav 音轨（ffmpeg 仅用于本脚本的解码步骤，不属于
   src/core/audio_timeline.py 的职责——该模块保持纯函数/无 I/O，符合 ARCHITECTURE.md#5）。
4. 用 audioop.rms 做简单的分帧能量检测，找到每段配音在最终音轨中的真实能量起点，
   与规划的 start_time 比较，断言偏差 < 0.2s。

退出码 0 = 通过；非 0 = 失败。
"""
from __future__ import annotations

import audioop
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.audio_timeline import build_timeline  # noqa: E402
from src.core.edge_tts_provider import EdgeTTSProvider  # noqa: E402
from src.models import Segment  # noqa: E402
from src.utils.ffmpeg_locator import download_ffmpeg, find_ffmpeg  # noqa: E402

SAMPLE_RATE = 24000  # 与 edge-tts 默认输出采样率一致
SAMPLE_WIDTH = 2  # 16-bit PCM
CHANNELS = 1
MAX_OFFSET_DIFF_SECONDS = 0.2

# 能量检测参数：每帧 20ms，RMS 超过阈值视为"有声"
_FRAME_MS = 20
_RMS_SILENCE_THRESHOLD = 300  # 16-bit PCM 下的经验阈值，明显高于底噪、明显低于人声


def _resolve_ffmpeg() -> str:
    path = find_ffmpeg()
    if path:
        return path
    print("未找到 ffmpeg，正在下载 LGPL 静态构建……")
    return download_ffmpeg()


def _decode_mp3_to_pcm(ffmpeg_path: str, mp3_path: str) -> bytes:
    result = subprocess.run(
        [
            ffmpeg_path,
            "-v", "error",
            "-i", mp3_path,
            "-ar", str(SAMPLE_RATE),
            "-ac", str(CHANNELS),
            "-f", "s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=True,
    )
    return result.stdout


def _silence_pcm(duration_seconds: float) -> bytes:
    n_samples = max(0, round(duration_seconds * SAMPLE_RATE))
    return b"\x00" * (n_samples * SAMPLE_WIDTH * CHANNELS)


def _trim_pcm(pcm: bytes, duration_seconds: float) -> bytes:
    n_samples = max(0, round(duration_seconds * SAMPLE_RATE))
    n_bytes = n_samples * SAMPLE_WIDTH * CHANNELS
    return pcm[:n_bytes]


def _detect_energy_onset(pcm: bytes, search_start: float, search_end: float) -> float:
    """在 [search_start, search_end) 秒范围内，找到第一个 RMS 超过阈值的帧的起始时间。"""
    frame_bytes = int(SAMPLE_RATE * (_FRAME_MS / 1000.0)) * SAMPLE_WIDTH * CHANNELS
    start_byte = max(0, round(search_start * SAMPLE_RATE)) * SAMPLE_WIDTH * CHANNELS
    end_byte = min(len(pcm), round(search_end * SAMPLE_RATE) * SAMPLE_WIDTH * CHANNELS)

    offset = start_byte
    while offset + frame_bytes <= end_byte:
        frame = pcm[offset : offset + frame_bytes]
        rms = audioop.rms(frame, SAMPLE_WIDTH)
        if rms > _RMS_SILENCE_THRESHOLD:
            return offset / (SAMPLE_WIDTH * CHANNELS) / SAMPLE_RATE
        offset += frame_bytes

    raise RuntimeError(
        f"在 [{search_start:.2f}s, {search_end:.2f}s) 范围内未检测到超过阈值的能量"
    )


def main() -> int:
    ffmpeg_path = _resolve_ffmpeg()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        provider = EdgeTTSProvider()

        raw_segments = [
            ("第一段测试旁白，用于校验能量起点是否准确。", 1.0),
            ("第二段测试旁白，起始时间与第一段不重叠。", 6.0),
        ]

        segments = []
        for i, (text, start_time) in enumerate(raw_segments):
            out_path = tmp_path / f"seg_{i}.mp3"
            duration = provider.synthesize(text, "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(out_path))
            segments.append(
                Segment(text=text, start_time=start_time, duration=duration, audio_path=str(out_path))
            )
            print(f"段 {i}：start_time={start_time}s，synthesize 时长={duration:.3f}s")

        video_duration = max(seg.start_time + seg.duration for seg in segments) + 3.0
        plan = build_timeline(segments, video_duration)
        print(f"video_duration={video_duration:.3f}s，overflow={plan.overflow}")

        # 按布局拼接出完整 PCM 音轨
        pcm_chunks = []
        for item in plan.items:
            if item.kind == "silence":
                pcm_chunks.append(_silence_pcm(item.duration))
            else:
                seg = segments[item.segment_index]
                pcm = _decode_mp3_to_pcm(ffmpeg_path, seg.audio_path)
                pcm_chunks.append(_trim_pcm(pcm, item.duration))
        full_pcm = b"".join(pcm_chunks)

        mixed_wav_path = tmp_path / "mixed.wav"
        with wave.open(str(mixed_wav_path), "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(full_pcm)

        # 对每个 segment 项做能量起点检测，与规划的 start 比较
        max_diff = 0.0
        for item in plan.items:
            if item.kind != "segment":
                continue
            search_start = max(0.0, item.start - 0.5)
            search_end = min(item.end, item.start + 3.0)
            detected_onset = _detect_energy_onset(full_pcm, search_start, search_end)
            diff = abs(detected_onset - item.start)
            max_diff = max(max_diff, diff)
            print(
                f"段 {item.segment_index}：规划 start={item.start:.3f}s，"
                f"实测能量起点={detected_onset:.3f}s，偏差={diff:.3f}s"
            )
            if diff >= MAX_OFFSET_DIFF_SECONDS:
                print(f"FAIL — 段 {item.segment_index} 偏差超出阈值 {MAX_OFFSET_DIFF_SECONDS}s")
                return 1

        print(f"最大偏差 {max_diff:.3f}s（阈值 {MAX_OFFSET_DIFF_SECONDS}s）")
        print("PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
