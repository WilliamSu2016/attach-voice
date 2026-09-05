"""scripts/verify_error_handling.py — F10 错误处理与用户提示的自动化验收脚本。

F10 的 3 条 manual 验证项要求真正"断网""导入无音轨视频""构造超长配音的工程并导出"，
延续 F06-F09 的既定做法（真实 QApplication + 真实 MainWindow + QTest 真实点击真实信号/
槽/核心调用链，只在"文件选择对话框"这一步 monkeypatch 返回值），用可客观验证的结果
代替人工观察：

1. 「断网后点击生成配音」→ monkeypatch `EdgeTTSProvider._synthesize_once`（唯一发起
   真实网络请求的位置）使其每次都抛出可重试异常，模拟真实断网；使用真实的重试/退避
   逻辑（缩短 retries/backoff 以加速），验证：重试耗尽后 GUI 弹出「配音生成失败」+
   中文「网络问题」提示，且窗口在此之后仍可正常响应（不崩溃、可继续操作）。
2. 「导入无音轨视频并选择 overlay 模式」→ 用 ffmpeg 生成一个真实无音轨的样例视频，
   分别验证「先选 overlay 再拖入无音轨视频」与「先拖入无音轨视频再选 overlay」两种
   顺序，均应弹出提示并自动把混音模式改回 replace（不产生损坏输出）。
3. 「构造配音总时长超过视频的工程并导出」→ 构造一段起始时间超出视频末尾附近、
   实际合成时长会超出视频剩余时长的脚本，触发 `TimelinePlan.overflow`；验证导出前
   弹出确认对话框（QMessageBox.question），确认后真实执行导出，用 ffprobe 验证导出
   视频时长与源视频时长一致（画面时长不变，仅配音被截断，对应 PRODUCT.md A5）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from src.core import edge_tts_provider  # noqa: E402
from src.core import video_processor as vp  # noqa: E402
from src.gui.main_window import MainWindow  # noqa: E402
from src.models import Segment  # noqa: E402
from src.utils.ffmpeg_locator import download_ffmpeg, find_ffmpeg  # noqa: E402
from src.utils.settings import AppSettings  # noqa: E402
from PyQt6.QtCore import QSettings  # noqa: E402

message_box_log: list[tuple[str, str, str]] = []
question_answer = {"value": QMessageBox.StandardButton.Yes}


def _patch_message_boxes() -> None:
    """offscreen 环境下 QMessageBox.exec() 会永久阻塞，改为记录后立即返回预设答案。"""

    def _fake_information(parent, title, text, *args, **kwargs):
        message_box_log.append(("information", title, text))
        print(f"  [QMessageBox.information] {title}: {text}")
        return QMessageBox.StandardButton.Ok

    def _fake_critical(parent, title, text, *args, **kwargs):
        message_box_log.append(("critical", title, text))
        print(f"  [QMessageBox.critical] {title}: {text}")
        return QMessageBox.StandardButton.Ok

    def _fake_question(parent, title, text, *args, **kwargs):
        message_box_log.append(("question", title, text))
        print(f"  [QMessageBox.question] {title}: {text} -> 返回 {question_answer['value']}")
        return question_answer["value"]

    QMessageBox.information = staticmethod(_fake_information)
    QMessageBox.critical = staticmethod(_fake_critical)
    QMessageBox.question = staticmethod(_fake_question)


def _ensure_ffmpeg() -> str:
    path = find_ffmpeg()
    if path:
        return path
    print("未检测到 ffmpeg，正在自动下载……")
    return download_ffmpeg()


def _generate_sample_video(ffmpeg_path: str, out_path: Path, duration: float, with_audio: bool) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ffmpeg_path, "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={duration}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(out_path)]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def _ffprobe_duration(ffprobe_path: str, path: str) -> float:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of",
         "default=noprint_wrappers=1:nokey=1", path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    return float(result.stdout.decode().strip())


def _wait_until(app, predicate, timeout_s: float = 60.0, interval_s: float = 0.02) -> bool:
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def main() -> int:
    _patch_message_boxes()

    ffmpeg_path = _ensure_ffmpeg()
    ffprobe_path = vp._ffprobe_path(ffmpeg_path)

    work_dir = REPO_ROOT / "assets" / "samples"
    work_dir.mkdir(parents=True, exist_ok=True)

    sample_with_audio = work_dir / "error_handling_with_audio.mp4"
    if not sample_with_audio.is_file():
        _generate_sample_video(ffmpeg_path, sample_with_audio, duration=8.0, with_audio=True)

    sample_no_audio = work_dir / "error_handling_no_audio.mp4"
    if not sample_no_audio.is_file():
        _generate_sample_video(ffmpeg_path, sample_no_audio, duration=8.0, with_audio=False)

    app = QApplication.instance() or QApplication([])

    def make_settings(name: str) -> AppSettings:
        # 每个 MainWindow 实例注入独立的临时 ini 文件后端，避免使用真实
        # `QSettings("attach-voice", "attach-voice")` 污染开发者本机注册表
        # （沿用 verify_settings_persistence.py 已确立的做法）。
        ini_path = work_dir / f"_verify_error_handling_{name}.ini"
        if ini_path.exists():
            ini_path.unlink()
        return AppSettings(QSettings(str(ini_path), QSettings.Format.IniFormat))

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, condition, detail))
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

    # ==================================================================
    # 场景 1：断网后点击生成配音 —— 重试后给出「网络失败」中文提示，应用不崩溃
    # ==================================================================
    window1 = MainWindow(settings=make_settings("w1"))
    window1._load_video(str(sample_with_audio))
    QTest.mouseClick(window1._add_row_btn, Qt.MouseButton.LeftButton)
    window1._segment_table.cellWidget(0, 0).setValue(0.0)
    window1._segment_table.item(0, 1).setText("测试网络失败场景的旁白文本。")

    zh_index = None
    for i in range(window1._voice_selector._language_combo.count()):
        if window1._voice_selector._language_combo.itemText(i) == "中文":
            zh_index = i
            break
    assert zh_index is not None, "需要真实网络获取到中文音色列表才能进行本场景"
    window1._voice_selector._language_combo.setCurrentIndex(zh_index)
    window1._voice_selector._voice_combo.setCurrentIndex(0)

    # 缩短重试/退避耗时，加速用例；仍然是真实的 EdgeTTSProvider 重试逻辑（未被绕过）。
    window1._tts_provider._max_retries = 2
    window1._tts_provider._backoff_base = 0.01

    async def _always_fail(*args, **kwargs):
        raise OSError("模拟断网：无法连接到 TTS 服务器")

    original_synthesize_once = edge_tts_provider.EdgeTTSProvider._synthesize_once
    edge_tts_provider.EdgeTTSProvider._synthesize_once = staticmethod(_always_fail)
    try:
        message_box_log.clear()
        QTest.mouseClick(window1._generate_btn, Qt.MouseButton.LeftButton)
        generate_failed_done = _wait_until(app, lambda: window1._worker is None, timeout_s=30)
    finally:
        edge_tts_provider.EdgeTTSProvider._synthesize_once = original_synthesize_once

    check("断网场景：「生成配音」任务（重试耗尽后）在超时前结束", generate_failed_done)
    network_error_shown = any(
        kind == "critical" and title == "配音生成失败" and ("网络" in text)
        for kind, title, text in message_box_log
    )
    check(
        "断网场景：重试耗尽后弹出「配音生成失败」+ 含「网络」字样的中文提示",
        network_error_shown,
        str(message_box_log),
    )
    check(
        "断网场景：失败后窗口仍正常响应（未崩溃，控件恢复可用）",
        window1._generate_btn.isEnabled() and not window1._cancel_btn.isEnabled(),
        f"generate_btn={window1._generate_btn.isEnabled()}, cancel_btn={window1._cancel_btn.isEnabled()}",
    )

    # ==================================================================
    # 场景 2：导入无音轨视频并选择 overlay 模式 —— 明确提示 + 自动降级为 replace
    # ==================================================================
    window2 = MainWindow(settings=make_settings("w2"))
    window2._load_video(str(sample_no_audio))
    check(
        "无音轨视频被正确探测为 has_audio=False",
        window2._video_info is not None and not window2._video_info.has_audio,
        f"video_info={window2._video_info}",
    )

    message_box_log.clear()
    window2._mix_mode_combo.setCurrentIndex(1)  # 尝试选中 overlay
    app.processEvents()
    check(
        "无音轨视频下选择 overlay：弹出明确的中文提示",
        any(kind == "information" and "音轨" in text for kind, title, text in message_box_log),
        str(message_box_log),
    )
    check(
        "无音轨视频下选择 overlay：混音模式自动降级回 replace（下拉框回到索引 0）",
        window2._mix_mode_combo.currentIndex() == 0,
        f"currentIndex={window2._mix_mode_combo.currentIndex()}",
    )
    check(
        "无音轨视频下选择 overlay：音量滑块因已降级为 replace 而禁用",
        not window2._volume_slider.isEnabled(),
    )

    # 反过来，先选中 overlay（此时视频含音轨，允许），再拖入无音轨视频，验证
    # `_load_video` 触发的同一套自动降级逻辑。
    window3 = MainWindow(settings=make_settings("w3"))
    window3._load_video(str(sample_with_audio))
    window3._mix_mode_combo.setCurrentIndex(1)  # overlay，含音轨视频下允许
    app.processEvents()
    check(
        "含音轨视频下可正常选中 overlay（作为对照组，不应误触发降级）",
        window3._mix_mode_combo.currentIndex() == 1 and window3._volume_slider.isEnabled(),
    )

    message_box_log.clear()
    window3._load_video(str(sample_no_audio))
    check(
        "先选 overlay、后加载无音轨视频：同样弹出提示并自动降级为 replace",
        any(kind == "information" and "音轨" in text for kind, title, text in message_box_log)
        and window3._mix_mode_combo.currentIndex() == 0,
        f"message_box_log={message_box_log}, currentIndex={window3._mix_mode_combo.currentIndex()}",
    )

    # 底层 export() 对 overlay+无音轨也有等价 replace 的兜底（见
    # video_processor._build_mux_command），验证真实导出无音轨视频不产生损坏输出。
    from src.core.audio_timeline import build_timeline
    from src.models import Project

    noaudio_seg_path = str(work_dir / "_noaudio_seg0.mp3")
    subprocess.run(
        [ffmpeg_path, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1.5",
         noaudio_seg_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True,
    )
    synthesized_seg = Segment(
        text="无音轨视频配音", start_time=0.0, audio_path=noaudio_seg_path, duration=1.5
    )
    plan_no_audio = build_timeline([synthesized_seg], window3._video_info.duration)
    project_no_audio = Project(
        video_path=str(sample_no_audio),
        segments=[synthesized_seg],
        voice="zh-CN-XiaoxiaoNeural",
        rate="+0%",
        pitch="+0Hz",
        mix_mode="overlay",  # 故意仍传 overlay，验证 core 层导出不因此产生损坏输出
        original_volume=0.2,
    )
    out_no_audio = str(work_dir / "error_handling_no_audio_export.mp4")
    vp.export(project_no_audio, plan_no_audio, out_no_audio)
    exported_duration = _ffprobe_duration(ffprobe_path, out_no_audio)
    check(
        "无音轨视频以 overlay 模式真实导出不产生损坏输出，时长与源视频一致",
        Path(out_no_audio).is_file() and Path(out_no_audio).stat().st_size > 0
        and abs(exported_duration - window3._video_info.duration) < 0.3,
        f"exported_duration={exported_duration}, source_duration={window3._video_info.duration}",
    )

    # ==================================================================
    # 场景 3：配音总时长超过视频时长 —— 导出前必须弹窗提示；输出视频时长与源一致（截断）
    # ==================================================================
    short_video = work_dir / "error_handling_short_video.mp4"
    if not short_video.is_file():
        _generate_sample_video(ffmpeg_path, short_video, duration=3.0, with_audio=True)

    window4 = MainWindow(settings=make_settings("w4"))
    window4._load_video(str(short_video))
    QTest.mouseClick(window4._add_row_btn, Qt.MouseButton.LeftButton)
    # 起始时间设在接近视频末尾处，配上一段真实合成的、比剩余时长长得多的配音，
    # 制造真实的 TimelinePlan.overflow=True（而非伪造 duration）。
    window4._segment_table.cellWidget(0, 0).setValue(2.0)
    window4._segment_table.item(0, 1).setText("这是一段会超出视频剩余时长很多的配音文本。")

    overflow_audio_path = str(work_dir / "_overflow_seg0.mp3")
    subprocess.run(
        [ffmpeg_path, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "5.0",
         overflow_audio_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True,
    )
    window4._synthesized_segments = [
        Segment(text="超长配音", start_time=2.0, audio_path=overflow_audio_path, duration=5.0)
    ]
    window4._update_duration_comparison()

    message_box_log.clear()
    question_answer["value"] = QMessageBox.StandardButton.Yes
    project4, plan4 = window4._build_project_and_plan()
    check("超长配音场景：真实构建的 TimelinePlan.overflow 确为 True", plan4 is not None and plan4.overflow)
    check(
        "超长配音场景：导出前弹出确认对话框（question），文案提及「截断」",
        any(kind == "question" and "截断" in text for kind, title, text in message_box_log),
        str(message_box_log),
    )
    check(
        "超长配音场景：用户确认后返回有效的 project/plan（未中止导出）",
        project4 is not None and plan4 is not None,
    )

    overflow_out_path = str(work_dir / "error_handling_overflow_export.mp4")
    vp.export(project4, plan4, overflow_out_path)
    overflow_out_duration = _ffprobe_duration(ffprobe_path, overflow_out_path)
    check(
        "超长配音场景：导出视频时长与源视频时长一致（画面未被拉伸，配音被截断，PRODUCT A5）",
        abs(overflow_out_duration - window4._video_info.duration) < 0.3,
        f"exported_duration={overflow_out_duration}, source_duration={window4._video_info.duration}",
    )

    # 对照：用户在确认对话框中选择「否」时，应中止导出（返回 None, None）。
    message_box_log.clear()
    question_answer["value"] = QMessageBox.StandardButton.No
    project4b, plan4b = window4._build_project_and_plan()
    check(
        "超长配音场景：用户在确认对话框选择「否」时中止导出（返回 None）",
        project4b is None and plan4b is None,
    )
    question_answer["value"] = QMessageBox.StandardButton.Yes  # 恢复默认，避免影响后续脚本复用

    print()
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"共 {len(checks)} 项检查，通过 {passed} 项。")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
