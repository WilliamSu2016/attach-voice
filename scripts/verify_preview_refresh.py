#!/usr/bin/env python
"""scripts/verify_preview_refresh.py

缺陷回归验证：**生成配音 → 修改文本 → 再次生成配音 → 点击试听，播放的仍是第一次的配音。**

根因（两层，本脚本在真实 GUI 上同时验证两层都已修复）：
1. `_generate_narration()` 固定写到 `out_dir/seg{i}.mp3`，第二次生成原地覆盖第一次的文件，
   两次的 `Segment.audio_path` 完全相同。
2. `_on_preview_clicked()` 直接 `setSource(同一个 URL)`：QMediaPlayer 认为源未改变而跳过
   重新加载，继续播放已缓冲的旧音频；且不清空旧源时可能仍持有旧文件句柄。

验证手段（不 mock 关键路径）：
- 真实 `QApplication` + 真实 `MainWindow`（注入临时 ini 的 `AppSettings`，不污染注册表）
- 真实 `QTest.mouseClick` 触发真实按钮信号槽
- 真实 edge-tts 网络合成（两次文本长度差异明显，便于用 ffprobe 时长客观区分）
- 真实 `QMediaPlayer`：断言试听后 source 指向**第二次**的文件，且 mediaStatus 不是 InvalidMedia

客观区分"放的是新配音还是旧配音"的两类指标（缺一不可，由负向对照实验确定）：
- **第 1 层（磁盘）**：两次生成必须产出不同路径，且第一次的文件仍存在（未被原地覆盖）。
- **第 2 层（播放器缓存）**：必须断言 `QMediaPlayer.duration()`（**播放器自己报告的**时长），
  而不是用 ffprobe 查 `source()` 指向的磁盘文件。因为缺陷版本是**原地覆盖**，磁盘上的内容
  早已是新配音，ffprobe 无论如何都读到新时长；真正的用户可感症状是"播放器没重新加载、
  仍在放缓冲里的旧音频"，只有 `player.duration()` 能暴露它。
  两段文本长度差异悬殊（约 2.4s vs 22.1s），因此该断言有充分的区分度。

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

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtMultimedia import QMediaPlayer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from src.core import video_processor as vp  # noqa: E402
from src.gui.main_window import MainWindow  # noqa: E402
from src.utils.ffmpeg_locator import download_ffmpeg, find_ffmpeg  # noqa: E402
from src.utils.settings import AppSettings  # noqa: E402

SHORT_TEXT = "第一次的文本。"
LONG_TEXT = (
    "这是修改之后的第二版文本，内容明显更长，"
    "目的是让这一次合成出来的配音时长与第一次有肉眼可辨的差异，"
    "从而可以用 ffprobe 客观判断试听时播放的到底是哪一次的产物。"
)


def _patch_message_boxes() -> None:
    """offscreen 环境下模态弹窗会永久阻塞，替换为记录调用后立即返回。"""
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)


def _ensure_ffmpeg() -> str:
    path = find_ffmpeg()
    if path:
        return path
    print("未检测到 ffmpeg，正在自动下载 LGPL 静态构建……")
    return download_ffmpeg()


def _ffprobe_duration(ffprobe_path: str, media_path: str) -> float:
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", media_path],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
    )
    try:
        return float(result.stdout.decode().strip())
    except ValueError:
        return -1.0


def _rel(p: str) -> str:
    """只显示 `run-xxxx/seg0.mp3`：两次产物文件名相同，只有父目录能区分。"""
    path = Path(p)
    return f"{path.parent.name}/{path.name}"


def _wait_until(app, predicate, timeout_s: float = 90.0, interval_s: float = 0.05) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(interval_s)
    return False


def _make_settings(tmp_dir: Path, name: str) -> AppSettings:
    """注入基于临时 ini 文件的 QSettings，避免污染真实注册表（见 O13）。"""
    from PyQt6.QtCore import QSettings

    backing = QSettings(str(tmp_dir / f"{name}.ini"), QSettings.Format.IniFormat)
    return AppSettings(backing)


def main() -> int:
    _patch_message_boxes()
    ffmpeg_path = _ensure_ffmpeg()
    ffprobe_path = vp._ffprobe_path(ffmpeg_path)

    app = QApplication.instance() or QApplication([])
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append((name, bool(condition), detail))
        print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))

    with tempfile.TemporaryDirectory(prefix="verify-preview-") as tmp:
        tmp_dir = Path(tmp)
        window = MainWindow(settings=_make_settings(tmp_dir, "preview"))

        # 选择中文音色（真实调用 list_voices，走真实网络）
        zh_index = None
        for i in range(window._voice_selector._language_combo.count()):
            if window._voice_selector._language_combo.itemText(i) == "中文":
                zh_index = i
                break
        assert zh_index is not None, "未找到中文语言项"
        window._voice_selector._language_combo.setCurrentIndex(zh_index)
        window._voice_selector._voice_combo.setCurrentIndex(0)

        # ------------------------------------------------------------------
        # 第一次：输入短文本 → 真实点击「生成配音」→ 真实点击「试听」
        # ------------------------------------------------------------------
        QTest.mouseClick(window._add_row_btn, Qt.MouseButton.LeftButton)
        window._segment_table.cellWidget(0, 0).setValue(0.0)
        window._segment_table.item(0, 1).setText(SHORT_TEXT)

        QTest.mouseClick(window._generate_btn, Qt.MouseButton.LeftButton)
        check("第一次生成：异步任务完成", _wait_until(app, lambda: window._worker is None))

        first_path = window._synthesized_segments[0].audio_path
        first_duration = _ffprobe_duration(ffprobe_path, first_path)
        check("第一次生成：产出可被 ffprobe 探测的合法音频",
              Path(first_path).is_file() and first_duration > 0,
              f"path={_rel(first_path)}, duration={first_duration:.3f}s")

        QTest.mouseClick(window._preview_btn, Qt.MouseButton.LeftButton)
        app.processEvents()
        first_source = window._media_player.source().toLocalFile()
        check("第一次试听：播放器 source 指向第一次的产物",
              os.path.normcase(first_source) == os.path.normcase(first_path),
              f"source={_rel(first_source)}")

        # ------------------------------------------------------------------
        # 修改文本 → 第二次生成 → 再次试听（这是用户报告的缺陷场景）
        # ------------------------------------------------------------------
        window._segment_table.item(0, 1).setText(LONG_TEXT)

        QTest.mouseClick(window._generate_btn, Qt.MouseButton.LeftButton)
        check("第二次生成：异步任务完成", _wait_until(app, lambda: window._worker is None))

        second_path = window._synthesized_segments[0].audio_path
        second_duration = _ffprobe_duration(ffprobe_path, second_path)

        check("【核心】第二次生成产出的是新文件，未复用/覆盖第一次的路径",
              os.path.normcase(second_path) != os.path.normcase(first_path),
              f"first={_rel(first_path)}, second={_rel(second_path)}")
        check("第一次的产物仍存在（未被原地覆盖，播放器句柄安全）",
              Path(first_path).is_file(), f"{_rel(first_path)}")
        check("第二次合成时长明显长于第一次（证明确实按新文本重新合成）",
              second_duration > first_duration + 1.0,
              f"first={first_duration:.3f}s, second={second_duration:.3f}s")

        QTest.mouseClick(window._preview_btn, Qt.MouseButton.LeftButton)
        app.processEvents()
        played_source = window._media_player.source().toLocalFile()
        played_duration = _ffprobe_duration(ffprobe_path, played_source)

        check("【核心·第1层】第二次试听：播放器 source 已切换到第二次的产物",
              os.path.normcase(played_source) == os.path.normcase(second_path),
              f"source={_rel(played_source)}（缺陷版本此处会仍指向 {_rel(first_path)}）")
        check("第二次试听 source 指向的磁盘文件时长 == 新文本的时长",
              abs(played_duration - second_duration) < 0.05,
              f"played={played_duration:.3f}s, second={second_duration:.3f}s")

        # 第 2 层（QMediaPlayer 缓存）必须查播放器**自己报告**的时长，而不是用 ffprobe 查磁盘文件：
        # 缺陷版本是原地覆盖，磁盘上的文件内容早已是新配音，ffprobe 无论如何都读到新时长；
        # 真正的用户可感症状是"播放器没重新加载、仍在放缓冲里的旧音频"，只有 player.duration()
        # 能反映这一点。（此断言由本次负向对照实验补入：仅靠磁盘侧断言会漏判第 2 层。）
        loaded = _wait_until(
            app,
            lambda: window._media_player.mediaStatus() in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
            ) and window._media_player.duration() > 0,
            timeout_s=20,
        )
        player_duration = window._media_player.duration() / 1000.0
        check("播放器在超时前完成加载并报告时长", loaded,
              f"status={window._media_player.mediaStatus()}, duration={player_duration:.3f}s")
        check("【核心·第2层】播放器自身报告的时长 == 新配音时长（证明确实重新加载，未沿用旧缓冲）",
              abs(player_duration - second_duration) < 0.3,
              f"player={player_duration:.3f}s, 新配音={second_duration:.3f}s, "
              f"旧配音={first_duration:.3f}s（缺陷版本此处会报旧配音的 {first_duration:.3f}s）")

        check("试听：QMediaPlayer 未报 InvalidMedia",
              window._media_player.mediaStatus() != QMediaPlayer.MediaStatus.InvalidMedia,
              f"status={window._media_player.mediaStatus()}")

        window._media_player.stop()
        window.close()
        app.processEvents()

    passed = sum(1 for _, ok, _ in checks if ok)
    failed = len(checks) - passed
    print(f"\n合计 {len(checks)} 项：PASS {passed} / FAIL {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
