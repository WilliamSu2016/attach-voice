"""tests/test_main_window_preview.py

回归测试：修改文本后重新生成配音，试听仍播放旧配音（用户报告的缺陷）。

根因有两层，本文件覆盖可脱离 GUI 单测的第一层：
`_generate_narration()` 每次都把产物写到 `out_dir/seg{i}.mp3` 这个**固定路径**，
第二次生成会原地覆盖第一次的文件，导致：
  1. `Segment.audio_path` 两次完全相同 → `QMediaPlayer.setSource()` 收到同一个 URL，
     Qt 视其为"源未改变"而不重新加载，继续播放已缓冲的旧音频；
  2. Windows 上播放器可能仍持有该文件句柄，原地覆盖存在写入失败/读到旧内容的风险。

第二层（`_on_preview_clicked` 需在设置新源前 stop + 清空旧源）依赖真实 QMediaPlayer，
由 `scripts/verify_gui_flow.py` 覆盖。
"""
from __future__ import annotations

from pathlib import Path
from typing import List

from src.gui.main_window import _generate_narration
from src.models import Segment
from src.utils.async_worker import CancelToken


class _FakeTTSProvider:
    """记录每次 synthesize 的文本与输出路径，并真实写出可区分内容的文件。"""

    def __init__(self) -> None:
        self.calls: List[tuple] = []

    def synthesize(self, text, voice, rate, pitch, out_path):
        self.calls.append((text, voice, rate, pitch, out_path))
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text, encoding="utf-8")
        return 1.0

    def list_voices(self, language=None):
        return []


def _run(provider, segments, out_dir):
    return _generate_narration(
        provider,
        segments,
        "zh-CN-XiaoxiaoNeural",
        "+0%",
        "+0Hz",
        str(out_dir),
        lambda _p: None,
        CancelToken(),
    )


def test_regenerate_after_text_change_yields_new_audio_path(tmp_path):
    """两次生成必须产出不同的 audio_path，否则试听会播放到旧音频。"""
    provider = _FakeTTSProvider()

    first = _run(provider, [Segment(text="第一次的文本", start_time=0.0)], tmp_path)
    second = _run(provider, [Segment(text="修改后的文本", start_time=0.0)], tmp_path)

    assert first[0].audio_path != second[0].audio_path, (
        "两次生成复用了同一个 audio_path，QMediaPlayer 会因为源 URL 未变而继续播放旧配音"
    )


def test_regenerated_file_contains_new_text(tmp_path):
    """新产物文件的内容必须是修改后的文本，且旧产物不被原地覆盖。"""
    provider = _FakeTTSProvider()

    first = _run(provider, [Segment(text="第一次的文本", start_time=0.0)], tmp_path)
    second = _run(provider, [Segment(text="修改后的文本", start_time=0.0)], tmp_path)

    assert Path(second[0].audio_path).read_text(encoding="utf-8") == "修改后的文本"
    assert Path(first[0].audio_path).exists(), "旧产物被原地覆盖，播放器可能仍持有其句柄"
    assert Path(first[0].audio_path).read_text(encoding="utf-8") == "第一次的文本"


def test_multi_segment_paths_unique_within_and_across_runs(tmp_path):
    """多段场景：同一次生成内各段路径互不相同，跨两次生成也互不相同。"""
    provider = _FakeTTSProvider()
    segments = [
        Segment(text="第一段", start_time=0.0),
        Segment(text="第二段", start_time=5.0),
    ]

    first = _run(provider, segments, tmp_path)
    second = _run(provider, segments, tmp_path)

    first_paths = [s.audio_path for s in first]
    second_paths = [s.audio_path for s in second]

    assert len(set(first_paths)) == 2, "同一次生成内各段路径重复"
    assert not set(first_paths) & set(second_paths), "跨两次生成出现了路径复用"


def test_segment_metadata_preserved(tmp_path):
    """路径唯一化不得影响既有行为：文本/起始时间/时长仍正确回填。"""
    provider = _FakeTTSProvider()
    result = _run(provider, [Segment(text="内容", start_time=3.5)], tmp_path)

    assert result[0].text == "内容"
    assert result[0].start_time == 3.5
    assert result[0].duration == 1.0
    assert Path(result[0].audio_path).is_file()
