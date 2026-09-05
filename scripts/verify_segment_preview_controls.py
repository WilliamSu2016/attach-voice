#!/usr/bin/env python
"""scripts/verify_segment_preview_controls.py

真实 GUI + 真实 edge-tts + 真实 ffmpeg 端到端验证（用户需求）：
1. 每个分段行「删除」按钮左侧新增「试听」按钮，点击只播放该行对应的原始配音文件。
2. 全局「试听已生成配音」按钮改为按时间轴渲染出与「仅导出音频」/导出视频完全一致的
   完整音轨（含段间静音间隔、超长截断），再播放该渲染文件——而不是把各段原始文件
   掐头去尾按列表顺序拼接播放。
3. 「试听」「试听已生成配音」「仅导出音频」都不应以选择视频为前提——只有「导出视频」
   才需要真实视频文件用于混音。

判定手段：
- 真实选择一段合成视频（真实 `probe()`）+ 3 段真实 edge-tts 合成的配音，起始时间之间留有
  真实静音间隔。
- 点击某一行的「试听」按钮：断言播放器加载的就是该行原始配音文件（未被渲染替换）。
- 点击全局「试听已生成配音」按钮：等待真实 `WorkerThread`（内部调用真实 ffmpeg）渲染完成，
  用真实 ffprobe 校验渲染出的文件总时长 ≈ 视频时长（证明包含了静音间隔，不是简单拼接），
  并断言播放器最终加载的是渲染出的文件路径（不是任何一段原始配音文件）。

退出码 0 = 全部通过；非 0 = 有失败项。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PyQt6.QtCore import Qt, QSettings  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from src.gui.main_window import MainWindow  # noqa: E402
from src.utils.ffmpeg_locator import find_ffmpeg  # noqa: E402
from src.utils.settings import AppSettings  # noqa: E402

TEXTS = [
    "第一段，短。",
    "第二段的文本长度中等，用来产生一个中等时长的配音片段。",
    "第三段，也不长。",
]
# 起始时间之间留出明显大于任何一段真实合成时长的间隔，保证渲染结果中段间必然存在静音。
START_TIMES = [0.0, 15.0, 30.0]
VIDEO_DURATION = 40.0


def _patch_message_boxes() -> None:
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)


def _wait_until(app, predicate, timeout_s: float = 90.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def _make_settings(tmp_dir: Path) -> AppSettings:
    backing = QSettings(str(tmp_dir / "settings.ini"), QSettings.Format.IniFormat)
    return AppSettings(backing)


def _ffprobe_duration(ffmpeg_path: str, media_path: str) -> float:
    from src.core import video_processor as vp

    ffprobe_path = vp._ffprobe_path(ffmpeg_path)
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", media_path],
        stdout=subprocess.PIPE, timeout=30,
    )
    return float(result.stdout.decode().strip())


def _make_sample_video(ffmpeg_path: str, out_path: str, duration: float) -> None:
    cmd = [
        ffmpeg_path, "-y",
        "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        out_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


def main() -> int:
    _patch_message_boxes()
    app = QApplication.instance() or QApplication([])
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))
        print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    ffmpeg_path = find_ffmpeg()
    assert ffmpeg_path, "本机未找到 ffmpeg，无法进行真实端到端验证"

    with tempfile.TemporaryDirectory(prefix="verify-segpreview-") as tmp:
        tmp_dir = Path(tmp)
        sample_video_path = str(tmp_dir / "sample.mp4")
        _make_sample_video(ffmpeg_path, sample_video_path, VIDEO_DURATION)

        window = MainWindow(settings=_make_settings(tmp_dir))
        window._load_video(sample_video_path)
        check(
            "真实视频探测成功且时长符合预期",
            window._video_info is not None
            and abs(window._video_info.duration - VIDEO_DURATION) < 1.0,
            f"探测时长={window._video_info.duration if window._video_info else None}",
        )

        zh_index = None
        for i in range(window._voice_selector._language_combo.count()):
            if window._voice_selector._language_combo.itemText(i) == "中文":
                zh_index = i
                break
        assert zh_index is not None
        window._voice_selector._language_combo.setCurrentIndex(zh_index)
        window._voice_selector._voice_combo.setCurrentIndex(0)

        for i, text in enumerate(TEXTS):
            QTest.mouseClick(window._add_row_btn, Qt.MouseButton.LeftButton)
            window._segment_table.cellWidget(i, 0).setValue(START_TIMES[i])
            window._segment_table.item(i, 1).setText(text)

        check(
            "每行都有独立的「试听」按钮（删除按钮左侧）",
            all(window._segment_table.cellWidget(r, 2).text() == "试听" for r in range(3))
            and all(window._segment_table.cellWidget(r, 3).text() == "删除" for r in range(3)),
        )

        QTest.mouseClick(window._generate_btn, Qt.MouseButton.LeftButton)
        check("三段真实配音生成完成", _wait_until(app, lambda: window._worker is None))
        check(
            "三段均已获得真实合成音频路径",
            len(window._synthesized_segments) == 3
            and all(seg.audio_path for seg in window._synthesized_segments),
        )

        # ------------------------------------------------------------------
        # 1) 点击第 2 行的「试听」按钮：应只播放该行原始配音文件，不涉及任何渲染。
        # ------------------------------------------------------------------
        raw_seg1_path = window._synthesized_segments[1].audio_path
        QTest.mouseClick(window._segment_table.cellWidget(1, 2), Qt.MouseButton.LeftButton)
        app.processEvents()
        check(
            "点击第 2 行「试听」→ 播放器加载的是该行原始配音文件",
            window._media_player.source().toLocalFile().replace("\\", "/")
            == raw_seg1_path.replace("\\", "/"),
        )
        window._media_player.stop()

        # ------------------------------------------------------------------
        # 2) 点击全局「试听已生成配音」：应触发真实 ffmpeg 渲染完整时间轴音轨后播放该文件。
        # ------------------------------------------------------------------
        raw_paths = {seg.audio_path.replace("\\", "/") for seg in window._synthesized_segments}
        QTest.mouseClick(window._preview_btn, Qt.MouseButton.LeftButton)
        app.processEvents()

        render_done = _wait_until(app, lambda: window._worker is None, timeout_s=60)
        check("全局试听：真实 ffmpeg 渲染任务已完成", render_done)

        played_source = window._media_player.source().toLocalFile().replace("\\", "/")
        check(
            "全局试听：播放器加载的是渲染出的时间轴音轨文件（不是任何一段原始配音文件）",
            played_source not in raw_paths and played_source != "",
            f"played_source={played_source}",
        )

        if played_source:
            rendered_duration = _ffprobe_duration(ffmpeg_path, played_source)
            check(
                "渲染出的音轨总时长与视频时长一致（证明包含了段间静音间隔，不是首尾拼接）",
                abs(rendered_duration - VIDEO_DURATION) < 1.0,
                f"渲染时长={rendered_duration:.2f}s, 视频时长={VIDEO_DURATION:.2f}s",
            )

        window._media_player.stop()

        # ------------------------------------------------------------------
        # 3) 用户明确要求：「试听」「试听已生成配音」「仅导出音频」都不应以选择视频为前提。
        #    清空已选视频后重新点击全局试听，应正常渲染播放（不报错），且渲染出的时长
        #    改为以最后一段配音结束时间为准（不再等于原视频的 40 秒）。
        # ------------------------------------------------------------------
        window._video_path = None
        window._video_info = None
        errors_without_video: list[tuple[str, str]] = []
        window._show_error = lambda title, msg: errors_without_video.append((title, msg))

        QTest.mouseClick(window._preview_btn, Qt.MouseButton.LeftButton)
        app.processEvents()
        render_done_no_video = _wait_until(app, lambda: window._worker is None, timeout_s=60)
        check(
            "未选视频时点击全局「试听已生成配音」不报错、真实渲染完成",
            render_done_no_video and not errors_without_video,
            f"errors={errors_without_video}",
        )
        played_source_no_video = window._media_player.source().toLocalFile().replace("\\", "/")
        expected_natural_duration = max(
            seg.start_time + seg.duration for seg in window._synthesized_segments
        )
        if played_source_no_video:
            rendered_duration_no_video = _ffprobe_duration(ffmpeg_path, played_source_no_video)
            check(
                "未选视频时渲染出的音轨时长 = 最后一段配音结束时间（不再等于原视频时长）",
                abs(rendered_duration_no_video - expected_natural_duration) < 1.0,
                f"渲染时长={rendered_duration_no_video:.2f}s, 预期(最后一段结束时间)={expected_natural_duration:.2f}s",
            )
        window._media_player.stop()

        window.close()
        app.processEvents()

    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    print(f"\n合计 {len(checks)} 项：PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
