"""音频时间轴合成：按 start_time 铺放各段音频，间隙补静音，超出视频时长的部分截断。

依据 ARCHITECTURE.md#5 / PRODUCT.md D2：
- 画面时长绝不改动：`build_timeline` 的输出 `TimelinePlan.total_duration` 恒等于传入的
  `video_duration`；任何配音都不会让这个值发生变化，超出的部分一律截断。
- 本模块是纯函数、无 I/O：只根据 `Segment.duration`（TTS 合成后的实际音频时长）计算布局，
  不读写文件、不调用 ffmpeg，可在不生成任何实际音频的情况下完成布局计算与溢出判定（供
  UI 提前提示 PRODUCT.md#A5）。
- 不 import PyQt6（规则 B1），只依赖 `src.models.Segment`（规则 B3：core 可依赖 models）。

设计要点：
1. 输入的 `segments` 顺序不要求已按 `start_time` 排序（对应「乱序段」边界），内部会按
   `start_time` 做稳定排序（相同 start_time 保留原始相对顺序），但 `TimelineItem` 会记录
   该段在**原始输入列表**中的下标，供上层（GUI/持久化）回填结果。
2. 每段的可用时长优先被两类约束截断：
   - 该段的结束时间超过 `video_duration` → 截断到 `video_duration`。
   - 该段的结束时间超过下一段的 `start_time`（无论是否因排序而相邻）→ 截断到下一段起点，
     避免两段配音互相重叠播放。
   任一发生截断（包括整段因 `start_time >= video_duration` 而被整段丢弃）都会记录到
   `TimelinePlan.truncated_segment_indices`，并使 `TimelinePlan.overflow = True`。
3. 排布完成后，段与段之间、开头与结尾的空隙一律以 `TimelineItem(kind="silence")` 补齐，
   保证 `items` 首尾拼接后总时长精确等于 `video_duration`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from src.models import Segment

# 判定"是否发生了实质性截断"的浮点容差（秒），避免因浮点误差产生误报
_EPSILON = 1e-6


class AudioTimelineError(ValueError):
    """音频时间轴构建失败（输入参数不合法，如缺失 duration、负数时长等）。"""


@dataclass(frozen=True)
class TimelineItem:
    """时间轴上的一段内容：静音占位，或某个脚本段落的音频。"""

    kind: str  # "silence" | "segment"
    start: float  # 在最终音轨中的起始秒数
    end: float  # 在最终音轨中的结束秒数
    segment_index: Optional[int] = None  # 对应原始 segments 列表中的下标（silence 为 None）
    audio_path: Optional[str] = None  # 该段音频文件路径（silence 为 None）
    source_offset: float = 0.0  # 从原始音频第几秒开始截取（当前实现恒为 0，预留扩展）
    is_truncated: bool = False  # 该段是否被截断（未播放完整原始 duration）

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class TimelinePlan:
    """单一完整音轨规划：items 按时间顺序首尾相接，总时长恒等于视频时长。"""

    items: List[TimelineItem]
    total_duration: float  # 恒等于传入的 video_duration（D2：画面时长绝不改动）
    overflow: bool  # 是否存在任何因截断/丢弃而未完整播放的段落
    truncated_segment_indices: List[int] = field(default_factory=list)
    dropped_segment_indices: List[int] = field(default_factory=list)


def build_timeline(segments: List[Segment], video_duration: float) -> TimelinePlan:
    """按 start_time 铺放各段音频，返回不含实际音频数据的 TimelinePlan。"""
    if video_duration < 0:
        raise AudioTimelineError(f"video_duration 不能为负数：{video_duration}")

    if not segments:
        items = [_silence_item(0.0, video_duration)] if video_duration > _EPSILON else []
        return TimelinePlan(items=items, total_duration=video_duration, overflow=False)

    # 记录原始下标，再按 start_time 稳定排序（相同 start_time 保留原始相对顺序）
    indexed = list(enumerate(segments))
    for idx, seg in indexed:
        if seg.start_time < 0:
            raise AudioTimelineError(f"第 {idx} 段 start_time 不能为负数：{seg.start_time}")
        if seg.duration is None:
            raise AudioTimelineError(
                f"第 {idx} 段缺少 duration（需先完成 TTS 合成才能构建时间轴）"
            )
        if seg.duration < 0:
            raise AudioTimelineError(f"第 {idx} 段 duration 不能为负数：{seg.duration}")

    indexed.sort(key=lambda pair: pair[1].start_time)

    # 第一遍：逐段按 video_duration 裁剪，超出视频时长的整段丢弃
    starts: List[float] = []
    ends: List[float] = []
    orig_indices: List[int] = []
    audio_paths: List[Optional[str]] = []
    original_durations: List[float] = []
    dropped_segment_indices: List[int] = []

    for orig_idx, seg in indexed:
        if seg.start_time >= video_duration:
            dropped_segment_indices.append(orig_idx)
            continue
        starts.append(seg.start_time)
        ends.append(min(seg.start_time + seg.duration, video_duration))
        orig_indices.append(orig_idx)
        audio_paths.append(seg.audio_path)
        original_durations.append(seg.duration)

    # 第二遍：相邻段落若重叠（含 start_time 相同的情况），把前一段截断到后一段起点。
    # 排序后 starts 非递减，因此 ends[i] = starts[i+1] 恒 >= starts[i]，不会产生负时长。
    truncated_segment_indices: List[int] = list(dropped_segment_indices)
    for i in range(len(starts) - 1):
        if ends[i] > starts[i + 1] + _EPSILON:
            ends[i] = starts[i + 1]

    # 汇总每段是否被截断（结束时间早于「起始时间 + 原始 duration」即视为截断）
    for i in range(len(starts)):
        used_duration = ends[i] - starts[i]
        if used_duration < original_durations[i] - _EPSILON:
            truncated_segment_indices.append(orig_indices[i])

    # 组装最终 items：段落之间、开头与结尾的空隙补静音
    items: List[TimelineItem] = []
    cursor = 0.0
    for i in range(len(starts)):
        start, end = starts[i], ends[i]
        if end - start <= _EPSILON:
            # 完全被压缩为零长度（如多段 start_time 相同导致前段被完全吞没），跳过不产出音频项
            continue
        if start > cursor + _EPSILON:
            items.append(_silence_item(cursor, start))
        is_truncated = (end - start) < original_durations[i] - _EPSILON
        items.append(
            TimelineItem(
                kind="segment",
                start=start,
                end=end,
                segment_index=orig_indices[i],
                audio_path=audio_paths[i],
                is_truncated=is_truncated,
            )
        )
        cursor = end

    if video_duration - cursor > _EPSILON:
        items.append(_silence_item(cursor, video_duration))

    overflow = bool(truncated_segment_indices)
    return TimelinePlan(
        items=items,
        total_duration=video_duration,
        overflow=overflow,
        truncated_segment_indices=sorted(set(truncated_segment_indices)),
        dropped_segment_indices=sorted(set(dropped_segment_indices)),
    )


def _silence_item(start: float, end: float) -> TimelineItem:
    return TimelineItem(kind="silence", start=start, end=end)
