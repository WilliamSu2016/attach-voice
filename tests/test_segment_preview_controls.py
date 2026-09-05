"""tests/test_segment_preview_controls.py

覆盖以下行为（用户需求：逐段试听按钮 + 全局按钮按时间轴播放 + 试听/仅导出音频不强制要求视频）：

1. `SegmentTable`：每行新增「试听」按钮，位于「删除」按钮左侧；点击时发出
   `preview_row_requested(row)` 信号，且行号会随删除操作正确更新（不是构造时固定捕获的旧行号）。
2. `MainWindow`：
   - 每行「试听」按钮 → 只播放该行对应的原始配音文件，不做时间轴渲染。
   - 全局「试听已生成配音」按钮 → 与「仅导出音频」共用 `build_timeline` + `export_audio_only`
     渲染出与导出结果一致的完整时间轴音轨（含段间静音间隔、超长截断），再播放该渲染文件；
     未生成配音时给出错误提示，不会崩溃或误播放旧内容。
   - 「试听」「试听已生成配音」「仅导出音频」均**不要求先选择视频**——只有「导出视频」需要
     真实视频文件用于混音。未选视频时以最后一段配音的结束时间作为时间轴总长。

`MainWindow` 相关用例使用真实 `QApplication`（offscreen）+ 真实 `QMediaPlayer`，但对全局试听/
仅导出音频的渲染步骤（`WorkerThread` 调用真实 ffmpeg）做同步 stub 替换，避免单元测试依赖真实
网络合成/ffmpeg 耗时；真实端到端渲染+播放证据由 `scripts/verify_segment_preview_controls.py`
提供。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QFileDialog, QPushButton

from src.core.video_processor import VideoInfo
from src.gui.main_window import MainWindow
from src.gui.segment_table import SegmentTable
from src.models import Segment
from src.utils.settings import AppSettings


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    from PyQt6.QtCore import QSettings

    return AppSettings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


# ----------------------------------------------------------------------
# SegmentTable：逐行「试听」按钮
# ----------------------------------------------------------------------
def test_segment_table_has_preview_button_left_of_remove(app):
    table = SegmentTable()
    table.add_row(0.0, "文本")

    preview_btn = table.cellWidget(0, 2)
    remove_btn = table.cellWidget(0, 3)

    assert isinstance(preview_btn, QPushButton) and preview_btn.text() == "试听"
    assert isinstance(remove_btn, QPushButton) and remove_btn.text() == "删除"


def test_segment_table_preview_button_emits_current_row_index(app):
    """删除中间行后，剩余行的「试听」按钮点击必须报告更新后的行号，而非构造时的旧行号。"""
    table = SegmentTable()
    for i in range(3):
        table.add_row(float(i), f"第{i}段")

    table.removeRow(0)  # 删除第 0 行后，原第 1、2 行下移为第 0、1 行

    received = []
    table.preview_row_requested.connect(received.append)

    table.cellWidget(0, 2).click()
    table.cellWidget(1, 2).click()

    assert received == [0, 1]


# ----------------------------------------------------------------------
# MainWindow：单行试听（原始文件，不涉及时间轴）
# ----------------------------------------------------------------------
def _make_window_with_segments(app, settings, tmp_path, texts, *, with_video=True):
    window = MainWindow(settings=settings)
    if with_video:
        window._video_path = str(tmp_path / "input.mp4")
        window._video_info = VideoInfo(
            duration=10.0,
            width=1920,
            height=1080,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
            container="mov,mp4,m4a,3gp,3g2,mj2",
        )
    paths = []
    for i, text in enumerate(texts):
        p = tmp_path / f"seg{i}.mp3"
        p.write_bytes(b"\x00" * 16)  # 占位文件，仅用于验证路径逻辑，不依赖真实可解码音频
        paths.append(str(p))
    window._synthesized_segments = [
        Segment(text=t, start_time=float(i * 2), audio_path=paths[i], duration=1.0)
        for i, t in enumerate(texts)
    ]
    return window, paths


def test_row_preview_plays_only_that_segment(app, settings, tmp_path):
    window, paths = _make_window_with_segments(app, settings, tmp_path, ["段A", "段B", "段C"])

    window._on_segment_preview_requested(1)

    assert window._media_player.source().toLocalFile().replace("\\", "/") == paths[1].replace("\\", "/")


def test_row_preview_out_of_range_shows_error_not_crash(app, settings, tmp_path, monkeypatch):
    window, _ = _make_window_with_segments(app, settings, tmp_path, ["段A"])
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))

    window._on_segment_preview_requested(5)  # 越界（例如生成后又新增了行但未重新生成）

    assert len(errors) == 1


# ----------------------------------------------------------------------
# MainWindow：全局「试听已生成配音」— 按时间轴渲染后单文件播放
# ----------------------------------------------------------------------
def test_global_preview_does_not_require_video_selected(app, settings, tmp_path, monkeypatch):
    """用户明确要求：「试听」「试听已生成配音」「仅导出音频」都不应以选择视频为前提。
    未选视频时应正常构建 `TimelinePlan`（不报错），并以最后一段配音结束时间为时间轴总长。"""
    window, paths = _make_window_with_segments(
        app, settings, tmp_path, ["段A", "段B", "段C"], with_video=False
    )
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))

    captured_plans = []
    monkeypatch.setattr(
        "src.gui.main_window.WorkerThread",
        lambda fn, plan, out_path: captured_plans.append(plan),
    )
    monkeypatch.setattr(window, "_start_worker", lambda worker, on_finished, on_error=None: None)

    window._on_preview_clicked()

    assert errors == [], f"未选视频时不应报错：{errors}"
    assert len(captured_plans) == 1
    plan = captured_plans[0]
    expected_duration = max(seg.start_time + seg.duration for seg in window._synthesized_segments)
    assert plan.total_duration == pytest.approx(expected_duration)


def test_global_preview_requires_synthesized_segments(app, settings, tmp_path, monkeypatch):
    window, _ = _make_window_with_segments(app, settings, tmp_path, [])
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))

    window._on_preview_clicked()

    assert len(errors) == 1
    assert errors[0][0] == "试听失败"


def test_global_preview_renders_timeline_then_plays_result(app, settings, tmp_path, monkeypatch):
    """全局试听应复用 `build_timeline`+`export_audio_only`（与导出同一路径）渲染出单个音轨文件，
    再播放该文件——而不是把各段原始文件掐头去尾拼接播放。"""
    window, paths = _make_window_with_segments(app, settings, tmp_path, ["段A", "段B", "段C"])

    rendered_path = str(tmp_path / "rendered-timeline.mp3")
    captured_plans = []

    class _StubWorker:
        """同步替身：跳过真实 QThread/ffmpeg，只记录传入的 TimelinePlan 供断言。"""

        def __init__(self, fn, plan, out_path):
            captured_plans.append(plan)
            self._out_path = out_path

    def fake_start_worker(worker, on_finished, on_error=None):
        # 真实实现里 worker 是 WorkerThread(_export_audio_only_task, plan, preview_path)；
        # 这里直接调用 on_finished 模拟 ffmpeg 渲染成功完成。
        on_finished(rendered_path)

    monkeypatch.setattr("src.gui.main_window.WorkerThread", _StubWorker)
    monkeypatch.setattr(window, "_start_worker", fake_start_worker)

    window._on_preview_clicked()

    assert len(captured_plans) == 1
    plan = captured_plans[0]
    # 时间轴应覆盖整段视频时长，且按 start_time 顺序包含全部 3 段（而非首尾拼接）。
    assert pytest.approx(sum(item.duration for item in plan.items), rel=1e-6) == window._video_info.duration
    segment_paths_in_plan = [item.audio_path for item in plan.items if item.kind == "segment"]
    assert segment_paths_in_plan == paths

    assert window._media_player.source().toLocalFile().replace("\\", "/") == rendered_path.replace("\\", "/")


# ----------------------------------------------------------------------
# MainWindow：「仅导出音频」— 同样不要求先选择视频
# ----------------------------------------------------------------------
def test_export_audio_only_does_not_require_video_selected(app, settings, tmp_path, monkeypatch):
    window, paths = _make_window_with_segments(
        app, settings, tmp_path, ["段A", "段B"], with_video=False
    )
    errors = []
    monkeypatch.setattr(window, "_show_error", lambda title, msg: errors.append((title, msg)))
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(tmp_path / "out.mp3"), ""))
    )

    captured_plans = []
    monkeypatch.setattr(
        "src.gui.main_window.WorkerThread",
        lambda fn, plan, out_path: captured_plans.append(plan),
    )
    monkeypatch.setattr(window, "_start_worker", lambda worker, on_finished, on_error=None: None)

    window._on_export_audio_only_clicked()

    assert errors == [], f"未选视频时「仅导出音频」不应报错：{errors}"
    assert len(captured_plans) == 1
    plan = captured_plans[0]
    expected_duration = max(seg.start_time + seg.duration for seg in window._synthesized_segments)
    assert plan.total_duration == pytest.approx(expected_duration)
    segment_paths_in_plan = [item.audio_path for item in plan.items if item.kind == "segment"]
    assert segment_paths_in_plan == paths
