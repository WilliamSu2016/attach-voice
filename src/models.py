"""数据模型（纯数据容器）。

约束（ARCHITECTURE.md 规则 B2）：本模块不得 import 任何项目内模块，
也不得 import PyQt6；不含 I/O、不含 ffmpeg 调用。
"""

from dataclasses import dataclass, field


@dataclass
class Segment:
    text: str
    start_time: float
    audio_path: str | None = None
    duration: float | None = None
    subtitle_end_time: float | None = None


@dataclass
class Project:
    video_path: str
    segments: list[Segment] = field(default_factory=list)
    voice: str = ""
    rate: str = "+0%"
    pitch: str = "+0Hz"
    mix_mode: str = "replace"
    original_volume: float = 0.2
