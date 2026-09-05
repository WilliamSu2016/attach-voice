"""主窗口：编排视频选择、分段脚本、音色、混音与导出，不含业务算法（规则 B8）。

依据 ARCHITECTURE.md：
- core 层抛领域异常，本层捕获后翻译为中文 `QMessageBox` 提示（规则 B9）。
- 本层不直接 import subprocess / edge_tts（规则 B6/B10），所有耗时操作
  （TTS 合成、ffmpeg 探测与导出）都通过 `src.core.*` 暴露的函数调用。
- F08 起，「生成配音」「导出」「仅导出音频」均通过 `src.utils.async_worker.WorkerThread`
  在后台线程执行，GUI 线程保持响应（"UI 不冻结"）；`_generate_narration`/`_export_video_task`/
  `_export_audio_only_task` 是纯编排的模块级函数（无 Qt 依赖），只是把 core 层调用适配成
  `WorkerThread` 期望的 `target(*args, on_progress, cancel_token)` 约定，不构成"业务算法"。
- F09 起，音色/语速/音调/输出目录/混音模式/原声音量在启动时从 `src.utils.settings.AppSettings`
  读取并回填，用户改动时立即写回；本层只调用 `AppSettings` 暴露的强类型方法，不直接使用
  Qt 的配置持久化类（规则 B7：仅 `utils/settings.py` 允许使用该类）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable, List, Optional

from PyQt6.QtCore import QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from src.core.audio_timeline import AudioTimelineError, TimelinePlan, build_timeline
from src.core.edge_tts_provider import EdgeTTSProvider
from src.core.script_parser import ScriptParseError
from src.core.tts_provider import TTSNetworkError, TTSProvider
from src.core.video_processor import (
    ExportCancelledError,
    ExportFailedError,
    NoAudioStreamError,
    UnsupportedVideoFormatError,
    VideoInfo,
    VideoProcessorError,
    export as video_export,
    export_audio_only,
    probe,
)
from src.gui.segment_table import SegmentTable
from src.gui.voice_selector import VoiceSelector
from src.models import Project, Segment
from src.utils.async_worker import CancelToken, TaskCancelledError, WorkerThread
from src.utils.ffmpeg_locator import FFmpegNotFoundError
from src.utils.settings import AppSettings

_MIX_MODES = [("覆盖原声（replace）", "replace"), ("与原声混音（overlay）", "overlay")]


# ---------------------------------------------------------------------------
# 后台任务目标函数（纯编排，无 Qt 依赖，供 WorkerThread 在后台线程调用）
# ---------------------------------------------------------------------------


def _generate_narration(
    tts_provider: TTSProvider,
    segments: List[Segment],
    voice: str,
    rate: str,
    pitch: str,
    out_dir: str,
    on_progress: Callable[[float], None],
    cancel_token: CancelToken,
) -> List[Segment]:
    """逐段调用 TTSProvider 合成，每段之间轮询 `cancel_token`（无法中断单段合成中途，
    但可在段落边界及时响应取消，满足 PRODUCT.md R5「任务可取消」）。
    """
    synthesized: List[Segment] = []
    total = len(segments)
    for i, seg in enumerate(segments):
        if cancel_token.is_cancelled():
            raise TaskCancelledError("生成配音已被用户取消。")
        out_path = str(Path(out_dir) / f"seg{i}.mp3")
        duration = tts_provider.synthesize(seg.text, voice, rate, pitch, out_path)
        synthesized.append(
            Segment(text=seg.text, start_time=seg.start_time, audio_path=out_path, duration=duration)
        )
        on_progress((i + 1) / total if total else 1.0)
    return synthesized


def _export_video_task(
    project: Project,
    plan: TimelinePlan,
    out_path: str,
    on_progress: Callable[[float], None],
    cancel_token: CancelToken,
) -> dict:
    """包装 `video_processor.export()`：`on_reencode_fallback` 在后台线程被调用，
    直接操作 Qt 控件不安全，因此先收集提示文案，导出完成后随 `finished` 信号一并
    交回 GUI 线程展示（回退重编码本身仍在导出过程中真实发生，只是提示文案延后到
    任务结束时显示，不影响 D4 行为本身）。
    """
    reencode_messages: List[str] = []
    video_export(
        project,
        plan,
        out_path,
        on_progress=on_progress,
        cancel_token=cancel_token,
        on_reencode_fallback=reencode_messages.append,
    )
    return {"out_path": out_path, "reencode_messages": reencode_messages}


def _export_audio_only_task(
    plan: TimelinePlan,
    out_path: str,
    on_progress: Callable[[float], None],
    cancel_token: CancelToken,
) -> str:
    export_audio_only(plan, out_path, on_progress=on_progress, cancel_token=cancel_token)
    return out_path


class MainWindow(QMainWindow):
    def __init__(self, parent: Optional[QWidget] = None, settings: Optional[AppSettings] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("attach-voice — 视频旁白配音")
        self.setAcceptDrops(True)

        self._settings = settings if settings is not None else AppSettings()
        self._tts_provider = EdgeTTSProvider()
        self._video_path: Optional[str] = None
        self._video_info: Optional[VideoInfo] = None
        self._synthesized_segments: List[Segment] = []
        self._tmp_dir = tempfile.mkdtemp(prefix="attach-voice-gui-")
        self._worker: Optional[WorkerThread] = None

        self._media_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._media_player.setAudioOutput(self._audio_output)

        self._build_ui()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # 视频选择区
        video_row = QHBoxLayout()
        self._video_label = QLabel("尚未选择视频（可拖拽或点击「选择视频」）", self)
        self._select_video_btn = QPushButton("选择视频", self)
        self._select_video_btn.clicked.connect(self._on_select_video_clicked)
        video_row.addWidget(self._video_label, stretch=1)
        video_row.addWidget(self._select_video_btn)
        root_layout.addLayout(video_row)

        # 分段表格
        self._segment_table = SegmentTable(self)
        root_layout.addWidget(self._segment_table, stretch=1)

        table_btn_row = QHBoxLayout()
        self._add_row_btn = QPushButton("添加分段", self)
        self._add_row_btn.clicked.connect(lambda: self._segment_table.add_row())
        self._preview_btn = QPushButton("试听已生成配音", self)
        self._preview_btn.clicked.connect(self._on_preview_clicked)
        table_btn_row.addWidget(self._add_row_btn)
        table_btn_row.addWidget(self._preview_btn)
        root_layout.addLayout(table_btn_row)

        # 音色选择
        self._voice_selector = VoiceSelector(self._tts_provider, self)
        root_layout.addWidget(self._voice_selector)

        self._generate_btn = QPushButton("生成配音", self)
        self._generate_btn.clicked.connect(self._on_generate_clicked)
        root_layout.addWidget(self._generate_btn)

        # 时长对比
        self._duration_label = QLabel("视频 -- 秒 / 配音占用至 -- 秒", self)
        root_layout.addWidget(self._duration_label)

        # 混音模式 + 音量
        mix_row = QHBoxLayout()
        self._mix_mode_combo = QComboBox(self)
        for label, _ in _MIX_MODES:
            self._mix_mode_combo.addItem(label)
        self._mix_mode_combo.currentIndexChanged.connect(self._on_mix_mode_changed)
        mix_row.addWidget(QLabel("混音模式", self))
        mix_row.addWidget(self._mix_mode_combo)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(20)  # 默认 0.2，对应 PRODUCT.md 默认原声音量
        self._volume_label = QLabel("原声音量 0.20", self)
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        mix_row.addWidget(self._volume_label)
        mix_row.addWidget(self._volume_slider)
        root_layout.addLayout(mix_row)
        self._apply_mix_mode_ui_state(_MIX_MODES[0][1])

        # 进度条 + 取消
        progress_row = QHBoxLayout()
        self._progress_bar = QProgressBar(self)
        self._cancel_btn = QPushButton("取消", self)
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        progress_row.addWidget(self._progress_bar, stretch=1)
        progress_row.addWidget(self._cancel_btn)
        root_layout.addLayout(progress_row)

        # 导出按钮
        export_row = QHBoxLayout()
        self._export_btn = QPushButton("导出", self)
        self._export_btn.clicked.connect(self._on_export_clicked)
        self._export_audio_btn = QPushButton("仅导出音频", self)
        self._export_audio_btn.clicked.connect(self._on_export_audio_only_clicked)
        export_row.addWidget(self._export_btn)
        export_row.addWidget(self._export_audio_btn)
        root_layout.addLayout(export_row)

        self._voice_selector.load_voices()
        self._load_settings()
        self._wire_settings_persistence()

    def _load_settings(self) -> None:
        """启动时从 `AppSettings` 读取并回填音色/语速/音调/混音模式/原声音量（PRODUCT.md R11）。
        缺失键时 `AppSettings` 各 getter 已自带合理默认值，这里无需再做防御性判断。
        """
        self._voice_selector.select_voice_by_short_name(self._settings.voice())
        self._voice_selector.set_rate(self._settings.rate())
        self._voice_selector.set_pitch(self._settings.pitch())

        mix_mode = self._settings.mix_mode()
        for index, (_, mode) in enumerate(_MIX_MODES):
            if mode == mix_mode:
                self._mix_mode_combo.setCurrentIndex(index)
                break
        self._apply_mix_mode_ui_state(mix_mode)

        self._volume_slider.setValue(int(round(self._settings.original_volume() * 100)))

    def _wire_settings_persistence(self) -> None:
        """用户改动音色/语速/音调/混音模式/原声音量时立即写回 `AppSettings`（输出目录在
        导出成功后另行持久化，见 `_on_export_finished`/`_on_export_audio_only_finished`）。
        """
        self._voice_selector._voice_combo.currentIndexChanged.connect(
            lambda _index: self._settings.set_voice(self._voice_selector.selected_voice_short_name())
        )
        self._voice_selector._rate_spin.valueChanged.connect(self._settings.set_rate)
        self._voice_selector._pitch_spin.valueChanged.connect(self._settings.set_pitch)

    def _on_volume_changed(self, value: int) -> None:
        self._volume_label.setText(f"原声音量 {value / 100:.2f}")
        self._settings.set_original_volume(value / 100.0)

    def _on_mix_mode_changed(self, index: int) -> None:
        _, mode = _MIX_MODES[index]
        if mode == "overlay" and self._video_info is not None and not self._video_info.has_audio:
            # 原视频无音轨，overlay 无原声可混（等价于 replace，见 video_processor.export()），
            # 这里在 GUI 层给出明确提示并强制回退选中项，避免用户误以为 overlay 生效。
            self._show_info(
                "提示", "当前视频不含音轨，无法使用「与原声混音（overlay）」，已自动切换为「覆盖原声（replace）」。"
            )
            self._mix_mode_combo.blockSignals(True)
            self._mix_mode_combo.setCurrentIndex(0)
            self._mix_mode_combo.blockSignals(False)
            mode = "replace"
        self._apply_mix_mode_ui_state(mode)
        self._settings.set_mix_mode(mode)

    def _enforce_mix_mode_for_video_info(self) -> None:
        """视频加载完成后，若当前已选中 overlay 但该视频无音轨，则复用
        `_on_mix_mode_changed` 的同一套提示 + 自动降级逻辑（不重复实现判断逻辑）。
        """
        if self._video_info is not None and not self._video_info.has_audio:
            current_index = self._mix_mode_combo.currentIndex()
            if _MIX_MODES[current_index][1] == "overlay":
                self._on_mix_mode_changed(current_index)

    def _apply_mix_mode_ui_state(self, mode: str) -> None:
        """仅根据混音模式更新音量滑块的启用状态，不触碰持久化配置（避免初始化/回填设置阶段
        意外覆盖尚未读取的已保存值，见 `_build_ui`/`_load_settings` 中的调用）。
        """
        self._volume_slider.setEnabled(mode == "overlay")

    # ------------------------------------------------------------------
    # 拖拽支持
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt 回调命名约定)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            self._load_video(urls[0].toLocalFile())

    # ------------------------------------------------------------------
    # 视频选择
    # ------------------------------------------------------------------
    def _on_select_video_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", "视频文件 (*.mp4 *.mov *.mkv *.avi);;所有文件 (*)"
        )
        if path:
            self._load_video(path)

    def _load_video(self, path: str) -> None:
        try:
            info = probe(path)
        except UnsupportedVideoFormatError as exc:
            self._show_error(
                "视频探测失败",
                f"该文件不受支持或已损坏，请检查视频格式（mp4/mov/mkv 等）后重试：{exc}",
            )
            return
        except (VideoProcessorError, FFmpegNotFoundError) as exc:
            self._show_error("视频探测失败", str(exc))
            return

        self._video_path = path
        self._video_info = info
        audio_desc = "含音轨" if info.has_audio else "不含音轨"
        self._video_label.setText(
            f"{Path(path).name} — {info.duration:.1f} 秒 / {info.width}x{info.height} / {audio_desc}"
        )
        self._update_duration_comparison()
        self._enforce_mix_mode_for_video_info()

    # ------------------------------------------------------------------
    # 后台任务的统一启动/收尾（规则 B4：具体业务由 target 决定，本类只做信号接线与 UI 状态切换）
    # ------------------------------------------------------------------
    def _set_busy(self, busy: bool) -> None:
        for widget in (
            self._select_video_btn,
            self._add_row_btn,
            self._preview_btn,
            self._generate_btn,
            self._export_btn,
            self._export_audio_btn,
            self._segment_table,
            self._voice_selector,
        ):
            widget.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)

    def _start_worker(
        self,
        worker: WorkerThread,
        on_finished: Callable[[Any], None],
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self._worker = worker
        self._progress_bar.setValue(0)

        def _handle_finished(result: Any) -> None:
            self._set_busy(False)
            self._worker = None
            on_finished(result)

        def _handle_error(exc: Exception) -> None:
            self._set_busy(False)
            self._worker = None
            if on_error is not None:
                on_error(exc)
            else:
                self._handle_task_error(exc)

        worker.progress.connect(
            lambda p: self._progress_bar.setValue(int(max(0.0, min(1.0, p)) * 100))
        )
        worker.finished.connect(_handle_finished)
        worker.error.connect(_handle_error)
        self._set_busy(True)
        worker.start()

    def _on_cancel_clicked(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _handle_task_error(self, exc: Exception) -> None:
        """core/async_worker 抛出的领域异常在此翻译为中文提示（规则 B9）。"""
        if isinstance(exc, (TaskCancelledError, ExportCancelledError)):
            self._show_info("已取消", str(exc) or "任务已被取消。")
        elif isinstance(exc, TTSNetworkError):
            self._show_error("配音生成失败", f"网络问题导致 TTS 合成失败：{exc}")
        elif isinstance(exc, NoAudioStreamError):
            self._show_error("导出失败", f"原视频不含音轨：{exc}")
        elif isinstance(exc, (ExportFailedError, VideoProcessorError, AudioTimelineError)):
            self._show_error("导出失败", str(exc))
        else:
            self._show_error("任务失败", str(exc))

    # ------------------------------------------------------------------
    # 生成配音
    # ------------------------------------------------------------------
    def _on_generate_clicked(self) -> None:
        try:
            self._segment_table.validate()
        except ScriptParseError as exc:
            self._show_error("脚本校验失败", str(exc))
            return

        segments = self._segment_table.get_segments()
        voice = self._voice_selector.selected_voice_short_name()
        if not voice:
            self._show_error("生成配音失败", "请先选择音色。")
            return
        rate = self._voice_selector.selected_rate()
        pitch = self._voice_selector.selected_pitch()

        worker = WorkerThread(
            _generate_narration, self._tts_provider, segments, voice, rate, pitch, self._tmp_dir
        )
        self._start_worker(worker, self._on_generate_finished)

    def _on_generate_finished(self, synthesized: List[Segment]) -> None:
        self._synthesized_segments = synthesized
        self._update_duration_comparison()
        QMessageBox.information(self, "生成配音", f"已生成 {len(synthesized)} 段配音。")

    def _update_duration_comparison(self) -> None:
        video_duration = self._video_info.duration if self._video_info else 0.0
        if self._synthesized_segments:
            narration_end = max(
                (s.start_time + (s.duration or 0.0)) for s in self._synthesized_segments
            )
        else:
            narration_end = 0.0
        self._duration_label.setText(
            f"视频 {video_duration:.1f} 秒 / 配音占用至 {narration_end:.1f} 秒"
        )

    # ------------------------------------------------------------------
    # 试听（简化：播放第一段已生成配音；逐段试听可后续通过表格内嵌按钮扩展）
    # ------------------------------------------------------------------
    def _on_preview_clicked(self) -> None:
        if not self._synthesized_segments:
            self._show_error("试听失败", "请先点击「生成配音」。")
            return
        audio_path = self._synthesized_segments[0].audio_path
        if not audio_path:
            self._show_error("试听失败", "该段尚未生成配音音频。")
            return
        self._media_player.setSource(QUrl.fromLocalFile(audio_path))
        self._media_player.play()

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _build_project_and_plan(self):
        if not self._video_path or not self._video_info:
            self._show_error("导出失败", "请先选择视频。")
            return None, None
        if not self._synthesized_segments:
            self._show_error("导出失败", "请先点击「生成配音」。")
            return None, None

        _, mix_mode = _MIX_MODES[self._mix_mode_combo.currentIndex()]
        voice = self._voice_selector.selected_voice_short_name() or ""
        project = Project(
            video_path=self._video_path,
            segments=self._synthesized_segments,
            voice=voice,
            rate=self._voice_selector.selected_rate(),
            pitch=self._voice_selector.selected_pitch(),
            mix_mode=mix_mode,
            original_volume=self._volume_slider.value() / 100.0,
        )
        try:
            plan = build_timeline(self._synthesized_segments, self._video_info.duration)
        except AudioTimelineError as exc:
            self._show_error("导出失败", f"时间轴规划失败：{exc}")
            return None, None

        if plan.overflow:
            # 配音总时长超出视频时长（或段落互相重叠）导致部分内容被截断：D2 规定画面时长
            # 绝不改动，因此只能截断配音，PRODUCT.md A5 要求导出前必须显式提示用户。
            proceed = QMessageBox.question(
                self,
                "配音将被截断",
                "部分配音内容超出视频时长（或与相邻段重叠），导出时将被截断，"
                "视频画面时长不会改变。是否继续导出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return None, None

        return project, plan

    def _on_export_clicked(self) -> None:
        project, plan = self._build_project_and_plan()
        if project is None or plan is None:
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "导出视频", self._settings.output_dir(), "MP4 视频 (*.mp4)"
        )
        if not out_path:
            return

        worker = WorkerThread(_export_video_task, project, plan, out_path)
        self._start_worker(worker, self._on_export_finished)

    def _on_export_finished(self, result: dict) -> None:
        self._progress_bar.setValue(100)
        self._settings.set_output_dir(str(Path(result["out_path"]).parent))
        for msg in result.get("reencode_messages", []):
            self._show_info("导出提示", msg)
        QMessageBox.information(self, "导出完成", f"已导出到：{result['out_path']}")

    def _on_export_audio_only_clicked(self) -> None:
        _, plan = self._build_project_and_plan()
        if plan is None:
            return

        out_path, _ = QFileDialog.getSaveFileName(
            self, "仅导出音频", self._settings.output_dir(), "MP3 音频 (*.mp3)"
        )
        if not out_path:
            return

        worker = WorkerThread(_export_audio_only_task, plan, out_path)
        self._start_worker(worker, self._on_export_audio_only_finished)

    def _on_export_audio_only_finished(self, out_path: str) -> None:
        self._progress_bar.setValue(100)
        self._settings.set_output_dir(str(Path(out_path).parent))
        QMessageBox.information(self, "导出完成", f"已导出到：{out_path}")

    # ------------------------------------------------------------------
    # 提示弹窗（core 层异常在此翻译为中文，规则 B9）
    # ------------------------------------------------------------------
    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _show_info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)
