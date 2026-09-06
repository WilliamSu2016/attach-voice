"""脚本解析与导出：.srt 与 [mm:ss]/[hh:mm:ss] 简易格式。

依据 ARCHITECTURE.md#5：本模块为纯函数，无 I/O、无网络，可脱离 GUI/网络直接单测。
校验规则：时间格式非法、起始时间非递增、段落重叠时抛出 ScriptParseError，
异常消息包含行号，便于用户定位问题（PRODUCT.md#R3、D1）。

设计说明：SRT 的显示结束时间存入 `Segment.subtitle_end_time`；`duration` 始终只表示
TTS 合成后的实际音频时长。`parse_simple` 得到的段落没有显式结束时间，两个字段均保持
为 None。
"""
from __future__ import annotations

import re
from typing import List, Optional

from src.models import Segment

# 简易格式默认显示时长（秒）：当 Segment.duration 为 None 时，to_srt 导出使用此值兜底，
# 并在存在下一段时截断到下一段起始时间之前，避免导出重叠字幕。
_DEFAULT_DISPLAY_DURATION = 2.0

_SRT_TIMESTAMP_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
_SRT_ARROW_RE = re.compile(r"^(\S+)\s*-->\s*(\S+)$")
_SIMPLE_LINE_RE = re.compile(r"^\[([^\]]*)\]\s?(.*)$")


class ScriptParseError(ValueError):
    """脚本解析失败。消息中包含出错的行号，便于用户定位。"""


# ---------------------------------------------------------------------------
# 时间戳解析辅助函数
# ---------------------------------------------------------------------------


def _parse_srt_timestamp(raw: str, line_no: int) -> float:
    match = _SRT_TIMESTAMP_RE.match(raw.strip())
    if not match:
        raise ScriptParseError(f'第 {line_no} 行：SRT 时间戳格式非法 -> "{raw}"')
    hh, mm, ss, ms = (int(g) for g in match.groups())
    if mm > 59 or ss > 59:
        raise ScriptParseError(
            f'第 {line_no} 行：SRT 时间戳数值越界（分/秒需为 0-59）-> "{raw}"'
        )
    return hh * 3600 + mm * 60 + ss + ms / 1000.0


def _parse_simple_time(raw: str, line_no: int) -> float:
    parts = raw.split(":")
    if len(parts) == 2:
        hh_str, mm_str, ss_str = "0", parts[0], parts[1]
    elif len(parts) == 3:
        hh_str, mm_str, ss_str = parts
    else:
        raise ScriptParseError(f'第 {line_no} 行：时间格式非法 -> "[{raw}]"')

    if not (hh_str.isdigit() and mm_str.isdigit() and ss_str.isdigit()):
        raise ScriptParseError(f'第 {line_no} 行：时间格式非法 -> "[{raw}]"')

    hh, mm, ss = int(hh_str), int(mm_str), int(ss_str)
    if mm > 59 or ss > 59:
        raise ScriptParseError(
            f'第 {line_no} 行：时间数值越界（分/秒需为 0-59）-> "[{raw}]"'
        )
    return hh * 3600 + mm * 60 + ss


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hh, rem_ms = divmod(total_ms, 3_600_000)
    mm, rem_ms = divmod(rem_ms, 60_000)
    ss, ms = divmod(rem_ms, 1000)
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def _check_monotonic_and_overlap(
    line_no: int, start: float, prev_start: Optional[float], prev_end: Optional[float]
) -> None:
    if prev_start is not None and start < prev_start:
        raise ScriptParseError(
            f"第 {line_no} 行：起始时间非递增（应 >= 前一段 {prev_start}s，实际 {start}s）"
        )
    if prev_end is not None and start < prev_end:
        raise ScriptParseError(
            f"第 {line_no} 行：段落与前一段重叠（前一段结束于 {prev_end}s，本段起始于 {start}s）"
        )


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_srt(text: str) -> List[Segment]:
    """解析标准 .srt 文本为 Segment 列表。"""
    lines = text.splitlines()
    segments: List[Segment] = []
    prev_start: Optional[float] = None
    prev_end: Optional[float] = None

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        # 可选的字幕序号行：纯数字则跳过，下一行应为时间戳行
        if line.isdigit():
            index_line_no = i + 1
            i += 1
            if i >= n:
                raise ScriptParseError(f"第 {index_line_no} 行：缺少时间戳行")
            line = lines[i].strip()

        time_line_no = i + 1
        arrow_match = _SRT_ARROW_RE.match(line)
        if not arrow_match:
            raise ScriptParseError(
                f'第 {time_line_no} 行：缺少 "-->" 时间戳行 -> "{lines[i]}"'
            )

        start = _parse_srt_timestamp(arrow_match.group(1), time_line_no)
        end = _parse_srt_timestamp(arrow_match.group(2), time_line_no)
        if end <= start:
            raise ScriptParseError(
                f"第 {time_line_no} 行：结束时间必须晚于起始时间（{end}s <= {start}s）"
            )

        i += 1
        text_lines: List[str] = []
        text_start_line_no = i + 1
        while i < n and lines[i].strip():
            text_lines.append(lines[i].strip())
            i += 1
        if not text_lines:
            raise ScriptParseError(f"第 {text_start_line_no} 行：字幕文本缺失")

        _check_monotonic_and_overlap(time_line_no, start, prev_start, prev_end)

        segments.append(
            Segment(text="\n".join(text_lines), start_time=start, subtitle_end_time=end)
        )
        prev_start, prev_end = start, end

        i += 1  # 跳过块之间的分隔空行

    return segments


def parse_simple(text: str) -> List[Segment]:
    """解析 `[mm:ss] 文本` 或 `[hh:mm:ss] 文本` 简易格式为 Segment 列表（每行一段）。"""
    segments: List[Segment] = []
    prev_start: Optional[float] = None

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        match = _SIMPLE_LINE_RE.match(line)
        if not match:
            raise ScriptParseError(f'第 {line_no} 行：时间格式非法 -> "{raw_line}"')

        time_part, segment_text = match.group(1), match.group(2).strip()
        start = _parse_simple_time(time_part, line_no)
        if not segment_text:
            raise ScriptParseError(f"第 {line_no} 行：文本内容缺失")

        _check_monotonic_and_overlap(line_no, start, prev_start, None)

        segments.append(Segment(text=segment_text, start_time=start))
        prev_start = start

    return segments


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def to_srt(segments: List[Segment]) -> str:
    """将 Segment 列表导出为标准 .srt 文本。"""
    if not segments:
        return ""

    _validate_srt_segments(segments)

    blocks: List[str] = []
    for idx, seg in enumerate(segments):
        start = seg.start_time
        if seg.subtitle_end_time is not None:
            end = seg.subtitle_end_time
        elif seg.duration is not None:
            end = start + seg.duration
        elif idx + 1 < len(segments):
            end = min(start + _DEFAULT_DISPLAY_DURATION, segments[idx + 1].start_time)
            if end <= start:
                end = start + 0.001
        else:
            end = start + _DEFAULT_DISPLAY_DURATION

        blocks.append(
            f"{idx + 1}\n"
            f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n"
            f"{seg.text}\n"
        )

    return "\n".join(blocks) + "\n"


def _validate_srt_segments(segments: List[Segment]) -> None:
    """校验 SRT 可表示的起止时间与顺序，错误使用表格行号（从 1 开始）。"""
    prev_start: Optional[float] = None
    prev_end: Optional[float] = None
    for row_no, seg in enumerate(segments, start=1):
        if not seg.text or not seg.text.strip():
            raise ScriptParseError(f"第 {row_no} 行：字幕文本缺失")
        if seg.start_time < 0:
            raise ScriptParseError(f"第 {row_no} 行：起始时间不能为负数（{seg.start_time}s）")
        if prev_start is not None and seg.start_time < prev_start:
            raise ScriptParseError(f"第 {row_no} 行：起始时间非递增")
        if seg.subtitle_end_time is not None:
            if seg.subtitle_end_time <= seg.start_time:
                raise ScriptParseError(f"第 {row_no} 行：结束时间必须晚于起始时间")
            if prev_end is not None and seg.start_time < prev_end:
                raise ScriptParseError(f"第 {row_no} 行：段落与前一段重叠")
            prev_end = seg.subtitle_end_time
        else:
            prev_end = None
        prev_start = seg.start_time


def validate_segments(segments: List[Segment]) -> None:
    """校验一组 Segment 是否可用（时间格式/非负性、文本非空、起始时间无歧义重复）。

    用于 F07 GUI 分段表格：表格是自由编辑的结构化行（而非从文本格式解析而来），因此
    不经过 `parse_srt`/`parse_simple` 的解析期校验，需要一个可直接作用于 `list[Segment]`
    的独立校验入口（规则 B8：GUI 层本身不实现业务校验算法，只调用本函数）。
    校验规则：
    - `start_time` 不能为负数
    - `text` 不能为空（去除首尾空白后）
    - 不允许两段 `start_time` 完全相同（顺序歧义，无法确定谁先播放）
    发现问题时抛出 `ScriptParseError`，消息包含以 1 起始的行号（表格行号，非文件行号）。
    """
    seen_start_times: dict[float, int] = {}
    for idx, seg in enumerate(segments):
        row_no = idx + 1
        if seg.start_time < 0:
            raise ScriptParseError(f"第 {row_no} 行：起始时间不能为负数（{seg.start_time}s）")
        if not seg.text or not seg.text.strip():
            raise ScriptParseError(f"第 {row_no} 行：文本内容不能为空")
        if seg.start_time in seen_start_times:
            raise ScriptParseError(
                f"第 {row_no} 行与第 {seen_start_times[seg.start_time]} 行起始时间相同"
                f"（{seg.start_time}s），顺序存在歧义，请修改其中一行的起始时间"
            )
        seen_start_times[seg.start_time] = row_no
