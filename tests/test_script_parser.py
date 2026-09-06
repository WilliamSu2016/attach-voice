"""tests/test_script_parser.py — src/core/script_parser.py 的纯单测。

覆盖：合法/非法时间格式、起始时间非递增、段落重叠、边界时间、
parse_srt / parse_simple / to_srt 的幂等往返。
"""
from __future__ import annotations

import pytest

from src.core.script_parser import (
    ScriptParseError,
    parse_simple,
    parse_srt,
    to_srt,
    validate_segments,
)
from src.models import Segment


# ---------------------------------------------------------------------------
# parse_srt — 合法输入
# ---------------------------------------------------------------------------


def test_parse_srt_basic_two_segments():
    text = (
        "1\n"
        "00:00:01,000 --> 00:00:04,000\n"
        "第一段文本\n"
        "\n"
        "2\n"
        "00:00:05,500 --> 00:00:07,250\n"
        "第二段文本\n"
    )
    segments = parse_srt(text)
    assert len(segments) == 2
    assert segments[0].text == "第一段文本"
    assert segments[0].start_time == pytest.approx(1.0)
    assert segments[0].duration is None
    assert segments[0].subtitle_end_time == pytest.approx(4.0)
    assert segments[1].text == "第二段文本"
    assert segments[1].start_time == pytest.approx(5.5)
    assert segments[1].duration is None
    assert segments[1].subtitle_end_time == pytest.approx(7.25)


def test_parse_srt_multiline_caption_is_preserved():
    text = "1\n00:00:00,000 --> 00:00:02,000\n第一行\n第二行\n"
    segments = parse_srt(text)
    assert len(segments) == 1
    assert segments[0].text == "第一行\n第二行"


def test_parse_srt_without_index_line():
    # 部分工具导出的 srt 省略序号行，也应能解析
    text = "00:00:00,000 --> 00:00:01,000\n仅有时间戳和文本\n"
    segments = parse_srt(text)
    assert len(segments) == 1
    assert segments[0].text == "仅有时间戳和文本"


def test_parse_srt_empty_text_returns_empty_list():
    assert parse_srt("") == []
    assert parse_srt("   \n\n  \n") == []


def test_parse_srt_adjacent_non_overlapping_is_valid():
    # 前一段结束时间恰好等于下一段起始时间，不算重叠
    text = (
        "1\n00:00:00,000 --> 00:00:02,000\nA\n\n"
        "2\n00:00:02,000 --> 00:00:04,000\nB\n"
    )
    segments = parse_srt(text)
    assert len(segments) == 2


# ---------------------------------------------------------------------------
# parse_srt — 非法输入
# ---------------------------------------------------------------------------


def test_parse_srt_invalid_timestamp_format_raises_with_line_number():
    text = "1\n00:00:01 --> 00:00:04,000\n文本\n"
    with pytest.raises(ScriptParseError, match="第 2 行"):
        parse_srt(text)


def test_parse_srt_timestamp_seconds_out_of_range_raises():
    text = "1\n00:00:99,000 --> 00:01:00,000\n文本\n"
    with pytest.raises(ScriptParseError, match="越界"):
        parse_srt(text)


def test_parse_srt_end_before_start_raises():
    text = "1\n00:00:05,000 --> 00:00:02,000\n文本\n"
    with pytest.raises(ScriptParseError, match="结束时间必须晚于起始时间"):
        parse_srt(text)


def test_parse_srt_missing_text_raises_with_line_number():
    text = "1\n00:00:01,000 --> 00:00:02,000\n"
    with pytest.raises(ScriptParseError, match="字幕文本缺失"):
        parse_srt(text)


def test_parse_srt_non_increasing_start_time_raises():
    text = (
        "1\n00:00:05,000 --> 00:00:06,000\nA\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nB\n"
    )
    with pytest.raises(ScriptParseError, match="非递增"):
        parse_srt(text)


def test_parse_srt_overlapping_segments_raises():
    text = (
        "1\n00:00:00,000 --> 00:00:05,000\nA\n\n"
        "2\n00:00:02,000 --> 00:00:06,000\nB\n"
    )
    with pytest.raises(ScriptParseError, match="重叠"):
        parse_srt(text)


def test_parse_srt_missing_timestamp_line_after_index_raises():
    text = "1\n"
    with pytest.raises(ScriptParseError, match="缺少时间戳行"):
        parse_srt(text)


def test_parse_srt_malformed_arrow_line_raises():
    text = "1\n不是时间戳行\n文本\n"
    with pytest.raises(ScriptParseError, match='缺少 "-->"'):
        parse_srt(text)


# ---------------------------------------------------------------------------
# parse_simple — 合法输入
# ---------------------------------------------------------------------------


def test_parse_simple_mmss_format():
    text = "[00:05] 第一段\n[01:30] 第二段\n"
    segments = parse_simple(text)
    assert len(segments) == 2
    assert segments[0].start_time == pytest.approx(5.0)
    assert segments[0].text == "第一段"
    assert segments[1].start_time == pytest.approx(90.0)
    assert segments[1].text == "第二段"
    assert segments[0].duration is None


def test_parse_simple_hhmmss_format():
    text = "[01:02:03] 带小时的段落\n"
    segments = parse_simple(text)
    assert len(segments) == 1
    assert segments[0].start_time == pytest.approx(3723.0)


def test_parse_simple_skips_blank_lines():
    text = "[00:00] A\n\n\n[00:10] B\n"
    segments = parse_simple(text)
    assert len(segments) == 2


def test_parse_simple_empty_text_returns_empty_list():
    assert parse_simple("") == []
    assert parse_simple("   \n\n") == []


# ---------------------------------------------------------------------------
# parse_simple — 非法输入 / 边界
# ---------------------------------------------------------------------------


def test_parse_simple_invalid_format_no_brackets_raises_with_line_number():
    text = "00:05 缺少方括号\n"
    with pytest.raises(ScriptParseError, match="第 1 行"):
        parse_simple(text)


def test_parse_simple_boundary_out_of_range_time_raises():
    # [99:99]：分/秒均越界，边界用例
    text = "[99:99] 非法时间\n"
    with pytest.raises(ScriptParseError, match="越界"):
        parse_simple(text)


def test_parse_simple_non_numeric_time_raises():
    text = "[ab:cd] 非数字时间\n"
    with pytest.raises(ScriptParseError, match="时间格式非法"):
        parse_simple(text)


def test_parse_simple_too_many_colon_segments_raises():
    text = "[1:2:3:4] 过多冒号\n"
    with pytest.raises(ScriptParseError, match="时间格式非法"):
        parse_simple(text)


def test_parse_simple_missing_text_raises():
    text = "[00:05]\n"
    with pytest.raises(ScriptParseError, match="文本内容缺失"):
        parse_simple(text)


def test_parse_simple_non_increasing_start_time_raises():
    text = "[00:10] A\n[00:05] B\n"
    with pytest.raises(ScriptParseError, match="非递增"):
        parse_simple(text)


def test_parse_simple_equal_start_time_is_allowed():
    # 起始时间相同（非"非递增"意义上的倒退）应允许，用于同一时刻多行旁白等场景
    text = "[00:10] A\n[00:10] B\n"
    segments = parse_simple(text)
    assert len(segments) == 2


# ---------------------------------------------------------------------------
# to_srt
# ---------------------------------------------------------------------------


def test_to_srt_empty_list_returns_empty_string():
    assert to_srt([]) == ""


def test_to_srt_with_duration_produces_expected_format():
    segments = [Segment(text="你好", start_time=1.0, duration=2.5)]
    result = to_srt(segments)
    assert result == "1\n00:00:01,000 --> 00:00:03,500\n你好\n\n"


def test_to_srt_without_duration_uses_default_and_clamps_to_next_start():
    segments = [
        Segment(text="A", start_time=0.0),
        Segment(text="B", start_time=1.0),
    ]
    result = to_srt(segments)
    # 默认显示时长 2s 会被截断到下一段起始时间 1.0s，避免重叠
    assert "00:00:00,000 --> 00:00:01,000" in result
    assert "00:00:01,000 --> 00:00:03,000" in result  # 最后一段无下一段，使用默认时长


def test_to_srt_without_duration_next_segment_same_start_falls_back_to_tiny_duration():
    # 极端场景：无 duration 且下一段起始时间与本段相同（理论上 parse_* 不会产生此输入，
    # 但 to_srt 作为独立公共函数需自行兜底，避免产出非法的 end<=start 时间戳）
    segments = [
        Segment(text="A", start_time=1.0),
        Segment(text="B", start_time=1.0),
    ]
    result = to_srt(segments)
    assert "00:00:01,000 --> 00:00:01,001" in result


# ---------------------------------------------------------------------------
# 幂等往返：to_srt(parse_srt(x))
# ---------------------------------------------------------------------------


def test_round_trip_srt_is_idempotent_in_time_and_text():
    original_text = (
        "1\n"
        "00:00:01,000 --> 00:00:04,500\n"
        "第一段旁白\n"
        "\n"
        "2\n"
        "00:00:05,000 --> 00:00:08,750\n"
        "第二段旁白\n"
    )
    first_pass = parse_srt(original_text)
    exported = to_srt(first_pass)
    second_pass = parse_srt(exported)

    assert len(first_pass) == len(second_pass)
    for a, b in zip(first_pass, second_pass):
        assert a.text == b.text
        assert a.start_time == pytest.approx(b.start_time)
        assert a.subtitle_end_time == pytest.approx(b.subtitle_end_time)


def test_to_srt_prefers_subtitle_end_time_over_tts_duration():
    segment = Segment(
        text="字幕显示两秒，配音可更长",
        start_time=1.0,
        duration=5.0,
        subtitle_end_time=3.0,
    )
    assert "00:00:01,000 --> 00:00:03,000" in to_srt([segment])


def test_to_srt_rejects_invalid_subtitle_order_and_overlap():
    with pytest.raises(ScriptParseError, match="非递增"):
        to_srt(
            [
                Segment(text="A", start_time=2.0, subtitle_end_time=3.0),
                Segment(text="B", start_time=1.0, subtitle_end_time=4.0),
            ]
        )
    with pytest.raises(ScriptParseError, match="重叠"):
        to_srt(
            [
                Segment(text="A", start_time=0.0, subtitle_end_time=3.0),
                Segment(text="B", start_time=2.0, subtitle_end_time=4.0),
            ]
        )


# ---------------------------------------------------------------------------
# validate_segments（供 F07 GUI 分段表格调用）
# ---------------------------------------------------------------------------


def test_validate_segments_accepts_valid_list():
    segments = [
        Segment(text="第一段", start_time=0.0),
        Segment(text="第二段", start_time=5.0),
    ]
    validate_segments(segments)  # 不应抛出


def test_validate_segments_empty_list_is_valid():
    validate_segments([])  # 不应抛出


def test_validate_segments_negative_start_time_raises():
    segments = [Segment(text="t", start_time=-1.0)]
    with pytest.raises(ScriptParseError, match="第 1 行"):
        validate_segments(segments)


def test_validate_segments_empty_text_raises():
    segments = [Segment(text="   ", start_time=0.0)]
    with pytest.raises(ScriptParseError, match="文本内容不能为空"):
        validate_segments(segments)


def test_validate_segments_duplicate_start_time_raises():
    segments = [
        Segment(text="a", start_time=2.0),
        Segment(text="b", start_time=2.0),
    ]
    with pytest.raises(ScriptParseError, match="第 2 行与第 1 行起始时间相同"):
        validate_segments(segments)


def test_validate_segments_out_of_order_start_times_allowed_if_distinct():
    # 校验不要求已排序，只要求无重复即可（乱序由 audio_timeline.build_timeline 处理）
    segments = [
        Segment(text="b", start_time=5.0),
        Segment(text="a", start_time=1.0),
    ]
    validate_segments(segments)  # 不应抛出
