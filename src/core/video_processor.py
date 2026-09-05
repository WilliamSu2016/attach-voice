"""视频探测、混音与导出：唯一允许调用 ffmpeg/ffprobe 子进程的模块（ARCHITECTURE.md 规则 B6）。

依据 PRODUCT.md D2/D4 与 ARCHITECTURE.md#5：
- **D2 画面绝不改动**：导出的视频画面（视频流）时长与源完全一致；音轨按 `TimelinePlan`
  （已由 `audio_timeline.build_timeline` 计算好、恒等于视频时长）铺放，超出部分已在时间轴
  规划阶段截断，本模块只负责“渲染”这份规划，不再做任何时长层面的画面裁剪/延长。
- **D4 不重编码视频**：默认导出命令使用 `-c:v copy -c:a aac`；仅当 ffmpeg 因容器/编码不兼容
  而失败时才回退到 `-c:v libx264` 重编码，并通过 `on_reencode_fallback` 回调通知上层（GUI
  负责翻译为「正在重新编码视频，耗时较长」提示，本层不弹窗，符合规则 B9）。
- **B5**：ffmpeg 可执行文件路径完全来自 `src/utils/ffmpeg_locator.find_ffmpeg`，本模块不得
  硬编码字符串 `"ffmpeg"`。ffprobe 路径由已解析出的 ffmpeg 路径推导（两者通常同目录分发），
  推导后同样先执行一次可用性校验，不新增独立的 PATH 搜索逻辑（`ffmpeg_locator.py` 仍是唯一
  的路径解析模块）。
- **B1**：不 import PyQt6。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from src.core.audio_timeline import TimelinePlan
from src.models import Project
from src.utils.ffmpeg_locator import FFmpegNotFoundError, find_ffmpeg, verify_ffmpeg

# 音频中间轨渲染时使用的采样参数（44.1kHz 立体声，兼容绝大多数容器与播放器）
_NARRATION_SAMPLE_RATE = 44100
_NARRATION_CHANNEL_LAYOUT = "stereo"

_TIME_RE = re.compile(r"time=(\d+):(\d{2}):(\d{2})(?:\.(\d+))?")


class VideoProcessorError(RuntimeError):
    """视频探测/导出过程中的领域异常基类（GUI 负责翻译为中文提示，本层不弹窗，见 B9）。"""


class NoAudioStreamError(VideoProcessorError):
    """视频不含音轨（overlay 模式下无法与原声混音时抛出）。"""


class UnsupportedVideoFormatError(VideoProcessorError):
    """ffprobe 无法解析该文件（容器/编码格式不受支持，或文件已损坏）。"""


class ExportCancelledError(VideoProcessorError):
    """导出任务被用户取消。"""


class ExportFailedError(VideoProcessorError):
    """ffmpeg 导出失败（含回退重编码后仍失败的情况）。"""


@dataclass(frozen=True)
class VideoInfo:
    """`probe()` 的探测结果。"""

    duration: float
    width: int
    height: int
    has_audio: bool
    video_codec: str
    audio_codec: Optional[str] = None
    container: str = ""  # ffprobe format_name，如 "mov,mp4,m4a,3gp,3g2,mj2"


class CancelToken:
    """线程安全的取消令牌：GUI/调用方可在另一线程调用 `cancel()` 中断正在进行的导出。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def _ffmpeg_path(user_configured_path: Optional[str] = None) -> str:
    path = find_ffmpeg(user_configured_path)
    if not path:
        raise FFmpegNotFoundError()
    return path


def _ffprobe_path(ffmpeg_path: str) -> str:
    """由已解析的 ffmpeg 路径推导 ffprobe 路径（同目录分发的通行约定）。"""
    ffmpeg_file = Path(ffmpeg_path)
    exe_suffix = ffmpeg_file.suffix  # ".exe" on Windows, "" elsewhere
    candidate = ffmpeg_file.with_name(f"ffprobe{exe_suffix}")
    if candidate.is_file() and verify_ffmpeg(candidate):
        return str(candidate)
    raise FFmpegNotFoundError(
        f"在 ffmpeg 所在目录未找到可用的 ffprobe：{candidate}。"
        "请确认 ffmpeg 发行包中同时包含 ffprobe。"
    )


def probe(path: str, user_configured_ffmpeg_path: Optional[str] = None) -> VideoInfo:
    """探测视频文件的时长、分辨率、是否含音轨、编码参数。"""
    ffmpeg_path = _ffmpeg_path(user_configured_ffmpeg_path)
    ffprobe_path = _ffprobe_path(ffmpeg_path)

    result = subprocess.run(
        [
            ffprobe_path,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    if result.returncode != 0:
        # ffprobe 无法解析该文件时以非零退出码返回：绝大多数情况是容器/编码格式不受
        # 支持，或文件已损坏；这两者从用户视角需要的提示是一致的（换个格式/换个文件），
        # 因此统一归类为 UnsupportedVideoFormatError，供 GUI 给出针对性的中文提示。
        raise UnsupportedVideoFormatError(
            f"ffprobe 无法解析此文件，可能不受支持的视频格式或文件已损坏（退出码 {result.returncode}）："
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )

    data = json.loads(result.stdout.decode("utf-8", errors="replace"))
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video_stream is None:
        raise UnsupportedVideoFormatError(f"文件不含视频流，可能不是受支持的视频格式：{path}")

    duration_str = fmt.get("duration") or video_stream.get("duration")
    if duration_str is None:
        raise UnsupportedVideoFormatError(f"无法从 ffprobe 输出中解析时长，文件可能已损坏：{path}")

    return VideoInfo(
        duration=float(duration_str),
        width=int(video_stream.get("width", 0)),
        height=int(video_stream.get("height", 0)),
        has_audio=audio_stream is not None,
        video_codec=str(video_stream.get("codec_name", "")),
        audio_codec=str(audio_stream.get("codec_name")) if audio_stream else None,
        container=str(fmt.get("format_name", "")),
    )


def _format_seconds_to_ffmpeg_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hh = int(seconds // 3600)
    mm = int((seconds % 3600) // 60)
    ss = seconds % 60
    return f"{hh:02d}:{mm:02d}:{ss:06.3f}"


def parse_progress_time(line: str) -> Optional[float]:
    """从一行 ffmpeg stderr 输出中解析 `time=HH:MM:SS.ms` 字段，返回已处理的秒数。"""
    match = _TIME_RE.search(line)
    if not match:
        return None
    hours, minutes, secs, frac = match.groups()
    total = int(hours) * 3600 + int(minutes) * 60 + int(secs)
    if frac:
        total += float(f"0.{frac}")
    return float(total)


def build_narration_filter_complex(
    plan: TimelinePlan, silence_input_offset: int = 0
) -> tuple[list[str], str]:
    """根据 TimelinePlan 构造用于渲染完整配音轨的 ffmpeg 输入参数与 filter_complex 表达式。

    返回 (extra_input_args, filter_complex_str)。`extra_input_args` 是需要追加在
    真实音频段输入之后的 `-f lavfi -t <dur> -i anullsrc=...` 参数列表（每个静音段一个
    虚拟输入）；真实音频段输入由调用方按 `plan.items` 中 kind == "segment" 的顺序追加
    `-i <audio_path>`。`silence_input_offset` 是真实音频段输入的数量（用于计算静音虚拟
    输入在整个 ffmpeg 命令里的下标）。
    """
    extra_input_args: list[str] = []
    labels: list[str] = []
    segment_input_index = 0
    silence_input_index = silence_input_offset

    filter_parts: list[str] = []
    for item in plan.items:
        if item.kind == "segment":
            in_label = f"{segment_input_index}:a"
            out_label = f"seg{segment_input_index}"
            # 段落若被截断（is_truncated），只截取到实际使用的 duration；否则原样使用。
            filter_parts.append(
                f"[{in_label}]atrim=0:{item.duration:.6f},asetpts=PTS-STARTPTS,"
                f"aformat=sample_rates={_NARRATION_SAMPLE_RATE}:channel_layouts={_NARRATION_CHANNEL_LAYOUT}"
                f"[{out_label}]"
            )
            labels.append(out_label)
            segment_input_index += 1
        else:
            extra_input_args += [
                "-f", "lavfi",
                "-t", f"{item.duration:.6f}",
                "-i", f"anullsrc=r={_NARRATION_SAMPLE_RATE}:cl={_NARRATION_CHANNEL_LAYOUT}",
            ]
            out_label = f"sil{silence_input_index}"
            filter_parts.append(f"[{silence_input_index}:a]anull[{out_label}]")
            labels.append(out_label)
            silence_input_index += 1

    concat_inputs = "".join(f"[{label}]" for label in labels)
    filter_parts.append(f"{concat_inputs}concat=n={len(labels)}:v=0:a=1[narration]")
    return extra_input_args, ";".join(filter_parts)


def _render_narration_track(
    ffmpeg_path: str,
    plan: TimelinePlan,
    narration_out_path: str,
    cancel_token: Optional[CancelToken] = None,
    cleanup_paths: Optional[List[str]] = None,
    codec_args: Optional[List[str]] = None,
    on_progress: Optional[Callable[[float], None]] = None,
) -> None:
    """将 TimelinePlan 渲染为单一完整配音轨，供后续与原视频混音（或直接作为独立音频导出）。

    与最终混音步骤共用 `_run_ffmpeg_with_progress` 执行，使取消能在渲染配音轨阶段
    （而不仅仅是最终混音阶段）就立即终止子进程并清理临时文件。`codec_args` 默认写
    `pcm_s16le`（供内部与视频混音使用的无损中间产物）；`export_audio_only` 会传入
    `["-c:a", "libmp3lame"]` 之类的参数直接产出可交付的音频文件（PRODUCT R10）。
    """
    segment_items = [item for item in plan.items if item.kind == "segment"]

    if not segment_items and not any(item.kind == "silence" for item in plan.items):
        raise VideoProcessorError("TimelinePlan 不含任何 items，无法渲染配音轨。")

    cmd: list[str] = [ffmpeg_path, "-y"]
    for item in segment_items:
        if not item.audio_path:
            raise VideoProcessorError(
                f"第 {item.segment_index} 段缺少 audio_path，无法渲染配音轨。"
            )
        cmd += ["-i", item.audio_path]

    extra_input_args, filter_complex = build_narration_filter_complex(
        plan, silence_input_offset=len(segment_items)
    )
    cmd += extra_input_args
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[narration]",
        *(codec_args if codec_args is not None else ["-c:a", "pcm_s16le"]),
        narration_out_path,
    ]

    stderr_text = _run_ffmpeg_with_progress(
        cmd,
        total_duration=plan.total_duration,
        on_progress=on_progress,
        cancel_token=cancel_token,
        cleanup_paths=cleanup_paths or [],
    )
    if not Path(narration_out_path).is_file() or Path(narration_out_path).stat().st_size == 0:
        raise VideoProcessorError(f"渲染配音轨失败：{stderr_text.strip()[-2000:]}")


def export_audio_only(
    timeline: TimelinePlan,
    out_path: str,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel_token: Optional[CancelToken] = None,
    user_configured_ffmpeg_path: Optional[str] = None,
) -> None:
    """仅导出完整配音轨为独立音频文件（不涉及原视频），对应 PRODUCT.md R10「仅导出音频」。

    输出格式按 `out_path` 后缀决定编码：`.mp3` 用 `libmp3lame`，其余默认沿用 `pcm_s16le`
    （即 wav）。与 `export()` 共用同一套渲染逻辑（`_render_narration_track`），因此同样
    支持通过 `cancel_token` 中断并清理临时文件、通过 `on_progress` 汇报进度。
    """
    if cancel_token is not None and cancel_token.is_cancelled():
        raise ExportCancelledError("导出已被用户取消。")

    ffmpeg_path = _ffmpeg_path(user_configured_ffmpeg_path)
    codec_args = ["-c:a", "libmp3lame"] if out_path.lower().endswith(".mp3") else ["-c:a", "pcm_s16le"]
    _render_narration_track(
        ffmpeg_path, timeline, out_path, cancel_token=cancel_token, cleanup_paths=[out_path],
        codec_args=codec_args, on_progress=on_progress,
    )


def _build_mux_command(
    ffmpeg_path: str,
    project: Project,
    video_info: VideoInfo,
    narration_path: str,
    out_path: str,
    reencode_video: bool = False,
) -> list[str]:
    """构造最终「原视频 + 配音轨」混音导出命令。"""
    cmd = [ffmpeg_path, "-y", "-i", project.video_path, "-i", narration_path]

    video_codec_args = ["-c:v", "libx264"] if reencode_video else ["-c:v", "copy"]

    if project.mix_mode == "overlay" and video_info.has_audio:
        filter_complex = (
            f"[0:a]volume={project.original_volume}[orig];"
            f"[orig][1:a]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd += [
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
        ]
    else:
        # replace 模式，或 overlay 但原视频本就没有音轨（等价于 replace）
        cmd += ["-map", "0:v", "-map", "1:a"]

    cmd += video_codec_args
    cmd += ["-c:a", "aac", "-shortest", str(out_path)]
    return cmd


_REENCODE_TRIGGER_PATTERNS = (
    "codec not currently supported in container",
    "could not find tag for codec",
    "invalid data found when processing input",
    "encoder not found",
)


def _looks_like_codec_incompatibility(stderr_text: str) -> bool:
    lowered = stderr_text.lower()
    return any(pattern in lowered for pattern in _REENCODE_TRIGGER_PATTERNS)


def _run_ffmpeg_with_progress(
    cmd: List[str],
    total_duration: float,
    on_progress: Optional[Callable[[float], None]],
    cancel_token: Optional[CancelToken],
    cleanup_paths: List[str],
) -> str:
    """执行 ffmpeg 命令，逐行解析 stderr 的 time= 字段回报进度；支持取消。

    返回完整 stderr 文本（用于失败时判断是否为编码不兼容，触发回退）。
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_lines: list[str] = []
    try:
        assert process.stderr is not None
        for line in process.stderr:
            stderr_lines.append(line)
            if cancel_token is not None and cancel_token.is_cancelled():
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                _cleanup(cleanup_paths)
                raise ExportCancelledError("导出已被用户取消。")
            processed = parse_progress_time(line)
            if processed is not None and on_progress is not None and total_duration > 0:
                on_progress(min(1.0, processed / total_duration))
        process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
    return "".join(stderr_lines)


def _cleanup(paths: List[str]) -> None:
    for p in paths:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass


def export(
    project: Project,
    timeline: TimelinePlan,
    out_path: str,
    on_progress: Optional[Callable[[float], None]] = None,
    cancel_token: Optional[CancelToken] = None,
    on_reencode_fallback: Optional[Callable[[str], None]] = None,
    user_configured_ffmpeg_path: Optional[str] = None,
) -> None:
    """按 `timeline` 规划渲染配音轨，与原视频混音导出到 `out_path`。

    - `replace` 模式丢弃原音轨；`overlay` 模式与原声按 `project.original_volume` 混音。
    - 默认 `-c:v copy`（不重编码画面，D4）；仅当因容器/编码不兼容导致 ffmpeg 失败才回退
      `-c:v libx264` 重编码，并调用 `on_reencode_fallback(message)` 通知上层。
    - `cancel_token.cancel()` 可从另一线程中断导出：终止 ffmpeg 子进程并清理临时文件。
    """
    ffmpeg_path = _ffmpeg_path(user_configured_ffmpeg_path)
    video_info = probe(project.video_path, user_configured_ffmpeg_path)

    if project.mix_mode == "overlay" and not video_info.has_audio:
        # 原视频没有音轨，overlay 退化为等价于 replace（无原声可混），不视为错误。
        pass

    with tempfile.TemporaryDirectory(prefix="attach-voice-export-") as tmp_dir:
        narration_path = str(Path(tmp_dir) / "narration.wav")
        cleanup_paths = [narration_path]

        if cancel_token is not None and cancel_token.is_cancelled():
            raise ExportCancelledError("导出已被用户取消。")

        _render_narration_track(
            ffmpeg_path, timeline, narration_path, cancel_token=cancel_token, cleanup_paths=cleanup_paths
        )

        if cancel_token is not None and cancel_token.is_cancelled():
            _cleanup(cleanup_paths)
            raise ExportCancelledError("导出已被用户取消。")

        cmd = _build_mux_command(
            ffmpeg_path, project, video_info, narration_path, out_path, reencode_video=False
        )
        stderr_text = _run_ffmpeg_with_progress(
            cmd, video_info.duration, on_progress, cancel_token, cleanup_paths
        )

        # -c:v copy 是否成功以退出码为准；_run_ffmpeg_with_progress 不返回退出码，这里重新判断
        # 输出文件是否生成且非空作为成功依据，更贴近真实 ffmpeg 行为（部分失败仍返回 0 输出的情形极少见）。
        if not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
            if _looks_like_codec_incompatibility(stderr_text):
                if on_reencode_fallback is not None:
                    on_reencode_fallback(
                        "检测到容器/编码不兼容，正在重新编码视频，耗时较长，请耐心等待。"
                    )
                cmd = _build_mux_command(
                    ffmpeg_path, project, video_info, narration_path, out_path, reencode_video=True
                )
                stderr_text = _run_ffmpeg_with_progress(
                    cmd, video_info.duration, on_progress, cancel_token, cleanup_paths
                )
                if not Path(out_path).is_file() or Path(out_path).stat().st_size == 0:
                    raise ExportFailedError(
                        f"回退重编码后导出仍失败：{stderr_text.strip()[-2000:]}"
                    )
            else:
                raise ExportFailedError(f"导出失败：{stderr_text.strip()[-2000:]}")
