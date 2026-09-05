"""tests/test_audio_timeline.py — src/core/audio_timeline.py 的纯单测。

覆盖：零段、补静音、超出 video_duration 截断、相邻段重叠截断、乱序段、
start_time 超出视频时长（整段丢弃）、完全重叠（零长度吞没）、参数校验异常。
"""
from __future__ import annotations

import pytest

from src.core.audio_timeline import AudioTimelineError, TimelineItem, build_timeline
from src.models import Segment


def _seg(text, start_time, duration, audio_path=None):
    return Segment(text=text, start_time=start_time, duration=duration, audio_path=audio_path)


# ---------------------------------------------------------------------------
# 零段 / 基本补静音
# ---------------------------------------------------------------------------


def test_build_timeline_empty_segments_is_all_silence():
    plan = build_timeline([], video_duration=10.0)
    assert plan.total_duration == 10.0
    assert plan.overflow is False
    assert plan.truncated_segment_indices == []
    assert len(plan.items) == 1
    assert plan.items[0].kind == "silence"
    assert plan.items[0].start == 0.0
    assert plan.items[0].end == 10.0


def test_build_timeline_empty_segments_zero_video_duration_no_items():
    plan = build_timeline([], video_duration=0.0)
    assert plan.items == []
    assert plan.total_duration == 0.0
    assert plan.overflow is False


def test_build_timeline_single_segment_with_leading_and_trailing_silence():
    segments = [_seg("你好", 2.0, 3.0)]
    plan = build_timeline(segments, video_duration=10.0)

    assert plan.overflow is False
    assert [item.kind for item in plan.items] == ["silence", "segment", "silence"]
    assert plan.items[0].start == 0.0 and plan.items[0].end == 2.0
    assert plan.items[1].start == 2.0 and plan.items[1].end == 5.0
    assert plan.items[1].segment_index == 0
    assert plan.items[1].is_truncated is False
    assert plan.items[2].start == 5.0 and plan.items[2].end == 10.0
    # items 首尾拼接总时长精确等于 video_duration
    assert plan.items[0].duration + plan.items[1].duration + plan.items[2].duration == pytest.approx(10.0)


def test_build_timeline_segment_starts_at_zero_no_leading_silence():
    segments = [_seg("A", 0.0, 4.0)]
    plan = build_timeline(segments, video_duration=4.0)
    assert [item.kind for item in plan.items] == ["segment"]
    assert plan.overflow is False


def test_build_timeline_multiple_non_overlapping_segments_with_gaps():
    segments = [_seg("A", 1.0, 1.0), _seg("B", 5.0, 1.0)]
    plan = build_timeline(segments, video_duration=10.0)
    kinds = [item.kind for item in plan.items]
    assert kinds == ["silence", "segment", "silence", "segment", "silence"]
    assert plan.overflow is False


# ---------------------------------------------------------------------------
# 超出 video_duration 截断
# ---------------------------------------------------------------------------


def test_build_timeline_truncates_segment_exceeding_video_duration():
    segments = [_seg("长配音", 8.0, 5.0)]  # 8s 起始 + 5s 时长 = 13s，超出 10s 视频
    plan = build_timeline(segments, video_duration=10.0)

    assert plan.overflow is True
    assert plan.truncated_segment_indices == [0]
    seg_item = next(i for i in plan.items if i.kind == "segment")
    assert seg_item.start == 8.0
    assert seg_item.end == 10.0
    assert seg_item.is_truncated is True
    assert plan.total_duration == 10.0  # 画面时长不受影响


def test_build_timeline_segment_start_time_beyond_video_duration_is_dropped():
    segments = [_seg("A", 1.0, 1.0), _seg("超出范围", 20.0, 2.0)]
    plan = build_timeline(segments, video_duration=10.0)

    assert plan.overflow is True
    assert plan.dropped_segment_indices == [1]
    assert plan.truncated_segment_indices == [1]
    assert all(item.segment_index != 1 for item in plan.items if item.kind == "segment")
    assert plan.total_duration == 10.0


def test_build_timeline_segment_start_time_equal_video_duration_is_dropped():
    segments = [_seg("边界", 10.0, 1.0)]
    plan = build_timeline(segments, video_duration=10.0)
    assert plan.dropped_segment_indices == [0]
    assert plan.overflow is True
    assert [item.kind for item in plan.items] == ["silence"]


# ---------------------------------------------------------------------------
# 相邻段重叠截断
# ---------------------------------------------------------------------------


def test_build_timeline_truncates_previous_segment_when_overlapping_next():
    # 第一段 0s 起、时长 5s（本应到 5s），但第二段 3s 就开始了 -> 第一段应被截断到 3s
    segments = [_seg("A", 0.0, 5.0), _seg("B", 3.0, 2.0)]
    plan = build_timeline(segments, video_duration=10.0)

    assert plan.overflow is True
    assert plan.truncated_segment_indices == [0]
    seg_items = [i for i in plan.items if i.kind == "segment"]
    assert seg_items[0].start == 0.0 and seg_items[0].end == 3.0
    assert seg_items[0].is_truncated is True
    assert seg_items[1].start == 3.0 and seg_items[1].end == 5.0
    assert seg_items[1].is_truncated is False


def test_build_timeline_adjacent_touching_segments_not_marked_truncated():
    # 第一段恰好在第二段起点结束，不算重叠也不算截断
    segments = [_seg("A", 0.0, 3.0), _seg("B", 3.0, 2.0)]
    plan = build_timeline(segments, video_duration=10.0)
    assert plan.overflow is False
    assert plan.truncated_segment_indices == []


def test_build_timeline_identical_start_time_fully_overlaps_and_swallows_previous():
    # 两段起始时间完全相同：先出现的那段被后一段完全吞没（零长度，不产出音频项）
    segments = [_seg("先", 2.0, 3.0), _seg("后", 2.0, 1.0)]
    plan = build_timeline(segments, video_duration=10.0)

    assert plan.overflow is True
    assert 0 in plan.truncated_segment_indices
    seg_items = [i for i in plan.items if i.kind == "segment"]
    assert len(seg_items) == 1
    assert seg_items[0].segment_index == 1
    assert seg_items[0].start == 2.0 and seg_items[0].end == 3.0


# ---------------------------------------------------------------------------
# 乱序段（输入未按 start_time 排序）
# ---------------------------------------------------------------------------


def test_build_timeline_handles_out_of_order_input_segments():
    segments = [_seg("后出现的段", 5.0, 1.0), _seg("先出现的段", 1.0, 1.0)]
    plan = build_timeline(segments, video_duration=10.0)

    seg_items = [i for i in plan.items if i.kind == "segment"]
    assert seg_items[0].start == 1.0 and seg_items[0].segment_index == 1
    assert seg_items[1].start == 5.0 and seg_items[1].segment_index == 0
    assert plan.overflow is False


# ---------------------------------------------------------------------------
# 参数校验异常
# ---------------------------------------------------------------------------


def test_build_timeline_negative_video_duration_raises():
    with pytest.raises(AudioTimelineError, match="video_duration"):
        build_timeline([], video_duration=-1.0)


def test_build_timeline_missing_duration_raises():
    segments = [Segment(text="A", start_time=0.0, duration=None)]
    with pytest.raises(AudioTimelineError, match="duration"):
        build_timeline(segments, video_duration=10.0)


def test_build_timeline_negative_start_time_raises():
    segments = [_seg("A", -1.0, 1.0)]
    with pytest.raises(AudioTimelineError, match="start_time"):
        build_timeline(segments, video_duration=10.0)


def test_build_timeline_negative_duration_raises():
    segments = [_seg("A", 0.0, -1.0)]
    with pytest.raises(AudioTimelineError, match="duration"):
        build_timeline(segments, video_duration=10.0)


# ---------------------------------------------------------------------------
# TimelineItem.duration 属性
# ---------------------------------------------------------------------------


def test_timeline_item_duration_property():
    item = TimelineItem(kind="segment", start=1.0, end=3.5, segment_index=0)
    assert item.duration == pytest.approx(2.5)
