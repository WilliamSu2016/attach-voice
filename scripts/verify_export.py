"""scripts/verify_export.py — F06/F11 引用的端到端导出验收脚本。

对应 PRODUCT.md 验收标准：
- `--assert-duration-delta <sec>`：A1，输出视频画面时长与源误差 < 给定阈值。
- `--sample 10min_1080p --assert-seconds <sec> --assert-no-reencode`：A3，
  10 分钟 1080p 视频导出耗时 < 给定阈值，且 ffprobe 确认视频流编码参数与源一致（未重编码）。
- `--all`：依次执行 A1、A2、A3、A5 全部断言（F11 端到端验收要求）。
  - A2 复用 `scripts/verify_timeline_offsets.py` 的能量起点检测逻辑（真实合成音频 + 拼接 PCM）。
  - A5 在核心层构造"配音总时长超出视频"的场景，真实导出后断言输出时长仍等于源视频时长
    （证明行为是截断配音，而非改动画面时长）；GUI 层的"导出前明确提示"部分已在 F10 的
    `scripts/verify_error_handling.py`（场景 3）中验证，此处不重复模拟 GUI。

本脚本不属于 `src/` 业务代码，是独立验证工具；直接调用 `src.core.video_processor` 的公开
函数完成真实导出（不 mock），符合本仓库既有验证脚本的定位（如 `verify_tts_duration.py`）。

样例视频不依赖仓库外部素材：用 ffmpeg `lavfi` 合成生成，并缓存到 `assets/samples/`
（首次运行需要生成，10 分钟 1080p 合成耗时视 CPU 而定，通常数十秒到几分钟；后续运行复用
缓存文件，不重复生成）。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.core import video_processor as vp  # noqa: E402
from src.core.audio_timeline import build_timeline  # noqa: E402
from src.core.edge_tts_provider import EdgeTTSProvider  # noqa: E402
from src.models import Project, Segment  # noqa: E402
from src.utils.ffmpeg_locator import download_ffmpeg, find_ffmpeg  # noqa: E402
import verify_timeline_offsets  # noqa: E402

SAMPLES_DIR = REPO_ROOT / "assets" / "samples"


def _ensure_ffmpeg() -> str:
    path = find_ffmpeg()
    if path:
        return path
    print("未检测到 ffmpeg，正在自动下载 LGPL 静态构建……")
    return download_ffmpeg()


def _generate_sample(ffmpeg_path: str, out_path: Path, width: int, height: int, duration: float) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path),
    ]
    print(f"生成合成样例视频：{out_path}（{width}x{height}, {duration}s）……")
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(
            f"生成样例视频失败：{result.stderr.decode('utf-8', errors='replace')}"
        )


def _get_or_generate_sample(ffmpeg_path: str, name: str, width: int, height: int, duration: float) -> Path:
    out_path = SAMPLES_DIR / f"{name}.mp4"
    if out_path.is_file():
        print(f"复用已缓存的样例视频：{out_path}")
        return out_path
    _generate_sample(ffmpeg_path, out_path, width, height, duration)
    return out_path


def _build_plan_and_project(video_path: str, video_duration: float, mix_mode: str = "replace"):
    provider = EdgeTTSProvider()
    tmp_audio = SAMPLES_DIR / "_verify_export_seg0.mp3"
    tmp_audio.parent.mkdir(parents=True, exist_ok=True)
    duration = provider.synthesize(
        "这是一段用于验证导出流程的测试配音。", "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(tmp_audio)
    )
    segments = [Segment(text="test", start_time=1.0, audio_path=str(tmp_audio), duration=duration)]
    plan = build_timeline(segments, video_duration=video_duration)
    project = Project(video_path=video_path, mix_mode=mix_mode)
    return project, plan, tmp_audio


def run_a1(threshold: float) -> bool:
    """A1：输出视频画面时长与原视频完全一致，误差 < threshold 秒。"""
    ffmpeg_path = _ensure_ffmpeg()
    sample_path = _get_or_generate_sample(ffmpeg_path, "a1_short_sample", 640, 480, 8.0)
    source_info = vp.probe(str(sample_path))

    project, plan, tmp_audio = _build_plan_and_project(str(sample_path), source_info.duration)
    out_path = SAMPLES_DIR / "_verify_export_a1_out.mp4"
    try:
        vp.export(project, plan, str(out_path))
        out_info = vp.probe(str(out_path))
        delta = abs(out_info.duration - source_info.duration)
        print(
            f"[A1] 源视频时长={source_info.duration:.3f}s，输出视频时长={out_info.duration:.3f}s，"
            f"误差={delta:.3f}s（阈值 {threshold}s）"
        )
        ok = delta < threshold
        print("[A1] PASS" if ok else "[A1] FAIL")
        return ok
    finally:
        tmp_audio.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def run_a3(sample_name: str, seconds_threshold: float, assert_no_reencode: bool) -> bool:
    """A3：10 分钟 1080p 视频导出耗时 < threshold 秒，且未重编码视频流。"""
    if sample_name != "10min_1080p":
        raise SystemExit(f"未知的 --sample 值：{sample_name}（当前只支持 10min_1080p）")

    ffmpeg_path = _ensure_ffmpeg()
    sample_path = _get_or_generate_sample(ffmpeg_path, "10min_1080p", 1920, 1080, 600.0)
    source_info = vp.probe(str(sample_path))
    print(
        f"源样例视频：{sample_path}，时长={source_info.duration:.1f}s，"
        f"分辨率={source_info.width}x{source_info.height}，编码={source_info.video_codec}"
    )

    project, plan, tmp_audio = _build_plan_and_project(str(sample_path), source_info.duration)
    out_path = SAMPLES_DIR / "_verify_export_a3_out.mp4"
    try:
        start = time.monotonic()
        vp.export(project, plan, str(out_path))
        elapsed = time.monotonic() - start

        out_info = vp.probe(str(out_path))
        print(
            f"[A3] 导出耗时={elapsed:.2f}s（阈值 {seconds_threshold}s）；"
            f"输出编码={out_info.video_codec} {out_info.width}x{out_info.height}"
        )

        ok = elapsed < seconds_threshold
        if assert_no_reencode:
            same_codec = (
                out_info.video_codec == source_info.video_codec
                and out_info.width == source_info.width
                and out_info.height == source_info.height
            )
            print(f"[A3] 未重编码断言：{'PASS' if same_codec else 'FAIL'}（视频流编码参数是否与源一致）")
            ok = ok and same_codec

        print("[A3] PASS" if ok else "[A3] FAIL")
        return ok
    finally:
        tmp_audio.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def run_a2() -> bool:
    """A2：各段配音在输出中的实际起始时间与设定值一致，偏差 < 0.2s。

    复用 `verify_timeline_offsets.main()` 的真实合成 + 能量起点检测逻辑（返回 0 表示通过）。
    """
    print("[A2] 运行 verify_timeline_offsets（真实合成配音 + 能量起点检测）……")
    code = verify_timeline_offsets.main()
    ok = code == 0
    print("[A2] PASS" if ok else "[A2] FAIL")
    return ok


def run_a5() -> bool:
    """A5：配音总时长超出视频时，实际行为为截断（画面时长不变），而非改动画面。

    GUI 层"导出前明确提示"已在 F10 的 scripts/verify_error_handling.py（场景 3）验证；
    此处只验证核心导出行为本身——构造一个配音明显长于视频的 overflow 场景，真实导出后
    断言输出视频时长仍等于源视频时长（证明是截断配音，未拉伸/改动画面）。
    """
    ffmpeg_path = _ensure_ffmpeg()
    sample_path = _get_or_generate_sample(ffmpeg_path, "a5_short_sample", 640, 480, 3.0)
    source_info = vp.probe(str(sample_path))

    provider = EdgeTTSProvider()
    tmp_audio = SAMPLES_DIR / "_verify_export_a5_seg0.mp3"
    tmp_audio.parent.mkdir(parents=True, exist_ok=True)
    duration = provider.synthesize(
        "这是一段刻意超长的测试配音，用于验证超出视频时长时导出会被截断而不是改动画面。"
        "继续朗读更多内容以确保合成时长明显超过三秒的视频画面长度。",
        "zh-CN-XiaoxiaoNeural", "+0%", "+0Hz", str(tmp_audio),
    )
    segments = [Segment(text="overflow-test", start_time=0.5, audio_path=str(tmp_audio), duration=duration)]
    plan = build_timeline(segments, video_duration=source_info.duration)
    project = Project(video_path=str(sample_path), mix_mode="replace")

    out_path = SAMPLES_DIR / "_verify_export_a5_out.mp4"
    try:
        print(
            f"[A5] 源视频时长={source_info.duration:.3f}s，配音合成时长={duration:.3f}s，"
            f"overflow={plan.overflow}"
        )
        ok_overflow = plan.overflow and duration > source_info.duration
        vp.export(project, plan, str(out_path))
        out_info = vp.probe(str(out_path))
        delta = abs(out_info.duration - source_info.duration)
        print(
            f"[A5] 输出视频时长={out_info.duration:.3f}s（应等于源视频时长，误差 < 0.05s，"
            f"证明是截断配音而非改动画面），误差={delta:.3f}s"
        )
        ok = ok_overflow and delta < 0.05
        print("[A5] PASS" if ok else "[A5] FAIL")
        return ok
    finally:
        tmp_audio.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assert-duration-delta", type=float, default=None, help="A1：时长误差阈值（秒）")
    parser.add_argument("--sample", type=str, default=None, help="A3：使用的合成样例视频名称，如 10min_1080p")
    parser.add_argument("--assert-seconds", type=float, default=None, help="A3：导出耗时阈值（秒）")
    parser.add_argument("--assert-no-reencode", action="store_true", help="A3：断言未触发视频重编码回退")
    parser.add_argument("--all", action="store_true", help="依次执行 A1 与 A3（供 F11 端到端验收复用）")
    args = parser.parse_args()

    results: list[bool] = []

    if args.all:
        results.append(run_a1(0.05))
        results.append(run_a2())
        results.append(run_a3("10min_1080p", 15.0, True))
        results.append(run_a5())
    else:
        if args.assert_duration_delta is not None:
            results.append(run_a1(args.assert_duration_delta))
        if args.sample is not None:
            results.append(
                run_a3(args.sample, args.assert_seconds or 15.0, args.assert_no_reencode)
            )

    if not results:
        parser.print_help()
        return 2

    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
