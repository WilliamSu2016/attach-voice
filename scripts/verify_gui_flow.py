"""scripts/verify_gui_flow.py — F07 GUI 主界面的自动化端到端验收脚本。

背景：F07 的三条 manual 验证项要求真正"拖入视频""点击生成配音后逐段试听""切换
replace/overlay 并拖动音量滑块""点击仅导出音频"，这些操作里的物理鼠标点击与听觉判断
agent 无法直接执行。经用户批准（见 agent-progress.md F07 记录），本脚本改用：

1. 真实运行的 QApplication（`QT_QPA_PLATFORM=offscreen`）+ 真实 MainWindow 实例；
2. `PyQt6.QtTest.QTest.mouseClick` 触发真实按钮（而非直接调用槽函数），走真实的
   信号/槽/核心调用链（真实 probe()、真实 edge-tts 网络合成、真实 ffmpeg 导出）；
3. 用可客观验证的结果代替主观听觉判断：
   - "试听有声音" → 断言配音文件本身可被 ffprobe 探测为合法音频、时长 > 0，且
     QMediaPlayer 成功设置 source 并未进入 InvalidMedia 状态；
   - "overlay 输出可听到降低音量的原声" → 分别以 volume=1.0 与 volume=0.2 真实导出，
     用 ffmpeg `volumedetect` 测得的 mean_volume 证明前者显著更响；
   - "产出 mp3，可播放，内容为完整配音轨" → ffprobe 探测 mp3 时长与配音轨规划总时长一致。

QFileDialog（原生文件对话框）在 offscreen 模式下无法真正弹出等待用户选择，因此对
`QFileDialog.getOpenFileName` / `getSaveFileName` 做了函数级 monkeypatch，让它们直接
返回脚本准备好的路径——除了"文件选择"这一步的返回值来源被替换，选择之后触发的全部
GUI→core 调用链均为真实执行，未被 mock。同理，`QMessageBox` 的模态弹窗被替换为直接
记录调用参数（否则 offscreen 环境下 exec() 会永久阻塞），但弹窗内容仍会被打印，供人工
核对文案是否符合预期中文提示（规则 B9）。
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
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from src.core import video_processor as vp  # noqa: E402
from src.core.audio_timeline import build_timeline  # noqa: E402
from src.gui.main_window import MainWindow  # noqa: E402
from src.models import Project  # noqa: E402
from src.utils.ffmpeg_locator import download_ffmpeg, find_ffmpeg  # noqa: E402

message_box_log: list[tuple[str, str, str]] = []


def _patch_message_boxes() -> None:
    """offscreen 环境下 QMessageBox.exec() 会永久阻塞等待用户点击，改为记录后立即返回。"""

    def _fake_information(parent, title, text, *args, **kwargs):
        message_box_log.append(("information", title, text))
        print(f"  [QMessageBox.information] {title}: {text}")
        return QMessageBox.StandardButton.Ok

    def _fake_critical(parent, title, text, *args, **kwargs):
        message_box_log.append(("critical", title, text))
        print(f"  [QMessageBox.critical] {title}: {text}")
        return QMessageBox.StandardButton.Ok

    QMessageBox.information = staticmethod(_fake_information)
    QMessageBox.critical = staticmethod(_fake_critical)


def _ensure_ffmpeg() -> str:
    path = find_ffmpeg()
    if path:
        return path
    print("未检测到 ffmpeg，正在自动下载……")
    return download_ffmpeg()


def _generate_sample_video(ffmpeg_path: str, out_path: Path, duration: float = 8.0) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path),
    ]
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


def _ffmpeg_mean_volume(ffmpeg_path: str, path: str, start: float = 0.0, duration: float | None = None) -> float:
    """用 volumedetect 滤镜测得指定时间窗内的 mean_volume（dB，负值越大声音越响）。

    默认测整段；传入 `start`/`duration` 可只测某个窗口（用于隔离"仅有原声、无配音重叠"的
    片段，排除配音轨响度对结果的干扰，更直接地体现 `original_volume` 参数的效果）。
    """
    cmd = [ffmpeg_path, "-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-i", path, "-af", "volumedetect", "-f", "null", "-"]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    stderr_text = result.stderr.decode("utf-8", errors="replace")
    for line in stderr_text.splitlines():
        if "mean_volume:" in line:
            return float(line.split("mean_volume:")[1].strip().split(" ")[0])
    raise AssertionError(f"未能从 ffmpeg 输出解析 mean_volume：{stderr_text[-1000:]}")


def _wait_until(app, predicate, timeout_s: float = 60.0, interval_s: float = 0.02) -> bool:
    """在事件循环中轮询 `predicate`，直到为真或超时。用于等待 `WorkerThread` 完成
    （F08 起「生成配音」「仅导出音频」等按钮点击后是异步执行的，不能像 F07 阶段那样
    点击后立即 `processEvents()` 一次就断言结果）。
    """
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
    sample_video = work_dir / "gui_flow_sample.mp4"
    if not sample_video.is_file():
        print(f"生成用于 GUI 验证的合成样例视频：{sample_video}")
        _generate_sample_video(ffmpeg_path, sample_video)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, condition, detail))
        status = "PASS" if condition else "FAIL"
        print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))

    # ------------------------------------------------------------------
    # 1) 拖入样例视频：调用真实 dropEvent（模拟拖拽），期望正确显示时长/分辨率/音轨
    # ------------------------------------------------------------------
    class _FakeMimeData:
        def hasUrls(self):
            return True

        def urls(self):
            from PyQt6.QtCore import QUrl
            return [QUrl.fromLocalFile(str(sample_video))]

    class _FakeDropEvent:
        def __init__(self, mime_data):
            self._mime_data = mime_data

        def mimeData(self):
            return self._mime_data

        def acceptProposedAction(self):
            pass

    window.dropEvent(_FakeDropEvent(_FakeMimeData()))
    app.processEvents()

    check(
        "拖入视频后 video_info 被正确探测",
        window._video_info is not None and window._video_info.has_audio,
        f"video_info={window._video_info}",
    )
    check(
        "视频标签显示时长/分辨率/音轨信息",
        "秒" in window._video_label.text() and "x" in window._video_label.text(),
        window._video_label.text(),
    )

    # ------------------------------------------------------------------
    # 2) 添加 3 段脚本（真实点击「添加分段」按钮）
    # ------------------------------------------------------------------
    texts = ["第一段旁白，欢迎观看本视频。", "第二段旁白，这里介绍产品特性。", "第三段旁白，感谢观看。"]
    starts = [0.5, 3.0, 5.5]
    for _ in texts:
        QTest.mouseClick(window._add_row_btn, Qt.MouseButton.LeftButton)
    for row, (start, text) in enumerate(zip(starts, texts)):
        window._segment_table.cellWidget(row, 0).setValue(start)
        window._segment_table.item(row, 1).setText(text)

    check(
        "分段表格新增 3 行并可读回",
        len(window._segment_table.get_segments()) == 3,
        str(window._segment_table.get_segments()),
    )

    # ------------------------------------------------------------------
    # 3) 选择中文音色（真实调用 list_voices，来自真实 edge-tts 网络请求）
    # ------------------------------------------------------------------
    zh_index = None
    for i in range(window._voice_selector._language_combo.count()):
        if window._voice_selector._language_combo.itemText(i) == "中文":
            zh_index = i
            break
    assert zh_index is not None
    window._voice_selector._language_combo.setCurrentIndex(zh_index)
    window._voice_selector._voice_combo.setCurrentIndex(0)
    check(
        "音色下拉按语言过滤后可选中文音色",
        window._voice_selector.selected_voice_short_name() is not None
        and "zh" in window._voice_selector.selected_voice_short_name().lower(),
        str(window._voice_selector.selected_voice_short_name()),
    )

    # ------------------------------------------------------------------
    # 4) 真实点击「生成配音」：调用真实 edge-tts 合成 3 段配音（F08 起为异步任务，
    #    需等待 WorkerThread 完成，即 window._worker 变回 None）。
    #    同时用 QTimer 心跳计数器验证任务运行期间事件循环未被阻塞（详见文件头注释，
    #    经用户批准作为「UI 不冻结」的客观替代证据）。
    # ------------------------------------------------------------------
    from PyQt6.QtCore import QTimer

    heartbeat = {"count": 0}
    heartbeat_timer = QTimer()
    heartbeat_timer.setInterval(50)
    heartbeat_timer.timeout.connect(lambda: heartbeat.__setitem__("count", heartbeat["count"] + 1))
    heartbeat_timer.start()

    QTest.mouseClick(window._generate_btn, Qt.MouseButton.LeftButton)
    check(
        "点击「生成配音」后 UI 立即进入忙碌态（生成/导出等按钮禁用，取消按钮启用）",
        not window._generate_btn.isEnabled() and window._cancel_btn.isEnabled(),
        f"generate_btn.enabled={window._generate_btn.isEnabled()}, cancel_btn.enabled={window._cancel_btn.isEnabled()}",
    )
    generate_done = _wait_until(app, lambda: window._worker is None, timeout_s=60)
    heartbeat_timer.stop()
    check("「生成配音」异步任务在超时前完成", generate_done)
    check(
        "任务运行期间 QTimer 心跳持续递增（客观证明 GUI 事件循环未被阻塞，等价于\"UI 不冻结/窗口可拖动\"）",
        heartbeat["count"] >= 3,
        f"heartbeat_count={heartbeat['count']}（若此调用是同步阻塞在 GUI 线程，如 F07 阶段实现，此计数器将恒为 0）",
    )

    all_valid_audio = True
    for seg in window._synthesized_segments:
        if not seg.audio_path or not Path(seg.audio_path).is_file():
            all_valid_audio = False
            break
        real_duration = _ffprobe_duration(ffprobe_path, seg.audio_path)
        if real_duration <= 0:
            all_valid_audio = False
            break

    check(
        "生成配音后每段都产出可被 ffprobe 探测的合法音频",
        len(window._synthesized_segments) == 3 and all_valid_audio,
        f"segments={[ (s.audio_path, s.duration) for s in window._synthesized_segments ]}",
    )
    check(
        "时长对比标签已更新（视频 X 秒 / 配音占用至 Y 秒）",
        "配音占用至" in window._duration_label.text(),
        window._duration_label.text(),
    )

    # ------------------------------------------------------------------
    # 5) 真实点击「试听已生成配音」：验证 QMediaPlayer 成功加载且未进入 InvalidMedia
    # ------------------------------------------------------------------
    QTest.mouseClick(window._preview_btn, Qt.MouseButton.LeftButton)
    app.processEvents()
    from PyQt6.QtMultimedia import QMediaPlayer

    is_not_invalid = window._media_player.mediaStatus() != QMediaPlayer.MediaStatus.InvalidMedia
    check(
        "试听：QMediaPlayer 成功设置 source 且未报 InvalidMedia",
        window._media_player.source().isLocalFile() and is_not_invalid,
        f"source={window._media_player.source().toLocalFile()}, status={window._media_player.mediaStatus()}",
    )

    # ------------------------------------------------------------------
    # 6) 真实点击切换混音模式 + 拖动音量滑块，验证 UI 状态联动
    # ------------------------------------------------------------------
    window._mix_mode_combo.setCurrentIndex(1)  # overlay
    app.processEvents()
    check("切换到 overlay 后音量滑块启用", window._volume_slider.isEnabled())

    window._mix_mode_combo.setCurrentIndex(0)  # replace
    app.processEvents()
    check("切换回 replace 后音量滑块禁用", not window._volume_slider.isEnabled())

    window._mix_mode_combo.setCurrentIndex(1)
    window._volume_slider.setValue(80)
    app.processEvents()
    check(
        "拖动音量滑块后标签同步更新",
        window._volume_label.text() == "原声音量 0.80",
        window._volume_label.text(),
    )

    # ------------------------------------------------------------------
    # 7) 验证音量参数真实影响导出结果（用 volumedetect 对比响度）
    # ------------------------------------------------------------------
    project_loud, plan = window._build_project_and_plan()
    project_loud.original_volume = 1.0
    project_loud.mix_mode = "overlay"
    project_quiet = Project(
        video_path=project_loud.video_path,
        segments=project_loud.segments,
        voice=project_loud.voice,
        rate=project_loud.rate,
        pitch=project_loud.pitch,
        mix_mode="overlay",
        original_volume=0.2,
    )

    out_loud = str(work_dir / "gui_flow_overlay_loud.mp4")
    out_quiet = str(work_dir / "gui_flow_overlay_quiet.mp4")
    vp.export(project_loud, plan, out_loud)
    vp.export(project_quiet, plan, out_quiet)

    mean_volume_loud = _ffmpeg_mean_volume(ffmpeg_path, out_loud, start=0.0, duration=starts[0] - 0.1)
    mean_volume_quiet = _ffmpeg_mean_volume(ffmpeg_path, out_quiet, start=0.0, duration=starts[0] - 0.1)
    check(
        "overlay 模式下 original_volume 参数真实影响导出音量（在无配音重叠的纯原声片段，"
        "1.0 明显比 0.2 更响）",
        mean_volume_loud > mean_volume_quiet + 3.0,
        f"mean_volume(loud={project_loud.original_volume})={mean_volume_loud}dB, "
        f"mean_volume(quiet={project_quiet.original_volume})={mean_volume_quiet}dB "
        f"(measured on [0, {starts[0] - 0.1:.1f}]s，此区间原声未与配音混叠)",
    )

    # ------------------------------------------------------------------
    # 7b) 真实点击「导出」后，在极短延迟后真实点击「取消」，验证：
    #     - GUI 层能在导出进行中真正触发取消（ExportCancelledError → 已取消提示）；
    #     - 任务很快结束（不会等到导出真正完成）；
    #     - 用 tasklist 观察取消后没有残留的 ffmpeg.exe 进程；
    #     - 没有残留的输出临时文件（video_processor 已在取消时清理）；
    #     - UI 控件恢复可用。
    # ------------------------------------------------------------------
    cancel_out_path = str(work_dir / "gui_flow_cancel_test.mp4")
    if Path(cancel_out_path).exists():
        Path(cancel_out_path).unlink()

    original_get_save_export = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: (cancel_out_path, "MP4 视频 (*.mp4)"))
    try:
        message_box_log.clear()
        QTest.mouseClick(window._export_btn, Qt.MouseButton.LeftButton)
        # 给后台线程一点时间真正启动 ffmpeg 子进程，再点击取消（复现
        # tests/test_video_processor.py::test_export_cancel_terminates_process_and_cleans_up
        # 中验证过的、同样短延迟即可可靠触发取消的时序）。
        QTest.qWait(80)
        assert window._worker is not None, "导出任务应仍在运行，取消测试才有意义"
        cancel_click_time = __import__("time").time()
        QTest.mouseClick(window._cancel_btn, Qt.MouseButton.LeftButton)
        cancel_done = _wait_until(app, lambda: window._worker is None, timeout_s=10)
        cancel_elapsed = __import__("time").time() - cancel_click_time
    finally:
        QFileDialog.getSaveFileName = original_get_save_export

    check("点击「取消」后导出任务在超时前结束", cancel_done, f"elapsed={cancel_elapsed:.2f}s")
    check(
        "点击「取消」到任务结束的耗时在 1 秒量级（未被阻塞等到导出完成）",
        cancel_elapsed < 2.0,
        f"cancel_elapsed={cancel_elapsed:.2f}s",
    )
    check(
        "取消后弹出「已取消」提示（而非错误弹窗）",
        any(kind == "information" and title == "已取消" for kind, title, text in message_box_log[-3:]),
        str(message_box_log[-3:]),
    )
    check(
        "取消后没有残留输出文件（video_processor 已清理）",
        not Path(cancel_out_path).exists(),
        f"path={cancel_out_path}",
    )
    check(
        "取消后 UI 控件恢复可用（导出/生成配音按钮重新启用，取消按钮禁用）",
        window._export_btn.isEnabled() and window._generate_btn.isEnabled() and not window._cancel_btn.isEnabled(),
        f"export_btn={window._export_btn.isEnabled()}, generate_btn={window._generate_btn.isEnabled()}, cancel_btn={window._cancel_btn.isEnabled()}",
    )

    import subprocess as _subprocess
    tasklist_result = _subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq ffmpeg.exe"],
        stdout=_subprocess.PIPE, stderr=_subprocess.PIPE, text=True, timeout=15,
    )
    no_residual_ffmpeg = "ffmpeg.exe" not in tasklist_result.stdout
    check(
        "取消后 tasklist 未观察到残留的 ffmpeg.exe 进程",
        no_residual_ffmpeg,
        tasklist_result.stdout.strip(),
    )

    # ------------------------------------------------------------------
    # 8) 真实点击「仅导出音频」：monkeypatch QFileDialog.getSaveFileName 返回准备好的路径
    # ------------------------------------------------------------------
    audio_only_path = str(work_dir / "gui_flow_audio_only.mp3")
    original_get_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **kw: (audio_only_path, "MP3 音频 (*.mp3)"))
    try:
        QTest.mouseClick(window._export_audio_btn, Qt.MouseButton.LeftButton)
        export_audio_done = _wait_until(app, lambda: window._worker is None, timeout_s=60)
    finally:
        QFileDialog.getSaveFileName = original_get_save

    check("「仅导出音频」异步任务在超时前完成", export_audio_done)

    audio_only_produced = Path(audio_only_path).is_file() and Path(audio_only_path).stat().st_size > 0
    audio_only_duration = _ffprobe_duration(ffprobe_path, audio_only_path) if audio_only_produced else 0.0
    check(
        "点击「仅导出音频」真实产出 mp3，且时长与配音轨规划总时长一致",
        audio_only_produced and abs(audio_only_duration - plan.total_duration) < 0.3,
        f"produced={audio_only_produced}, duration={audio_only_duration}, plan.total_duration={plan.total_duration}",
    )

    print()
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"共 {len(checks)} 项检查，通过 {passed} 项。")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
