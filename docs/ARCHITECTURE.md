# ARCHITECTURE.md — 技术架构与边界（稳定层）

> 本文件描述 **怎么做**：分层、模块边界、数据模型、依赖规则。
> 产品需求见 `PRODUCT.md`。实现前必须先读完这两份文件。

---

## 1. 技术栈（已钉死，不得擅自替换）

| 领域 | 选型 | 约束 |
|---|---|---|
| 语言 | Python 3.11+ | 使用 `dataclass`、`match`、类型注解 |
| GUI | PyQt6（含 `QtMultimedia`） | 试听依赖 QtMultimedia，打包时须显式验证 |
| TTS | `edge-tts` | 必须经由 `TTSProvider` 抽象访问 |
| 音视频 | ffmpeg / ffprobe | **subprocess 直接调用**，不使用 `ffmpeg-python` 等封装库（需捕获进度与取消） |
| 配置 | `QSettings` | 仅在 `utils/settings.py` 中使用 |
| 打包 | PyInstaller | 产物：Windows 单可执行程序 |

---

## 2. 分层架构与依赖规则

```
┌──────────────────────────────────────────┐
│  src/gui/          表现层 (PyQt6)         │  可依赖 core, utils, models
├──────────────────────────────────────────┤
│  src/core/         领域逻辑层              │  可依赖 utils, models
│                    (TTS/解析/时间轴/ffmpeg)│  **禁止 import PyQt6**
├──────────────────────────────────────────┤
│  src/utils/        基础设施层              │  仅可依赖 models
│  src/models.py     数据模型                │  零内部依赖
└──────────────────────────────────────────┘
```

### 硬性依赖规则（违反即视为实现失败）
1. **`src/core/**` 禁止 `import PyQt6`**（`async_worker.py` 除外，见下）。
   → 目的：核心逻辑可脱离 GUI 单元测试。
2. **`src/models.py` 不得 import 任何项目内模块**，也不得 import PyQt6。
3. **依赖方向单向**：`gui → core → utils → models`。禁止反向 import；禁止 `core` import `gui`。
4. **`utils/async_worker.py` 是唯一允许同时接触 PyQt6(QThread) 与 core 的适配器**，它属于 utils 层但仅被 gui 使用。
5. **只有 `utils/ffmpeg_locator.py` 负责解析 ffmpeg 可执行文件路径**；`core/video_processor.py` 必须通过它获取路径，不得硬编码 `"ffmpeg"`。
6. **只有 `core/video_processor.py` 允许 `subprocess` 调用 ffmpeg/ffprobe**（`ffmpeg_locator.py` 可调用 `ffmpeg -version` 做可用性校验）。
7. **只有 `utils/settings.py` 允许使用 `QSettings`**。
8. GUI 层不得包含业务算法（时间轴计算、解析、混音参数推导），只做编排与展示。

---

## 3. 仓库结构

```
attach-voice/
├── src/
│   ├── __main__.py              # 入口，以 `python -m src` 运行（避免相对导入在 PyInstaller 下出错）
│   ├── models.py                # Segment / Project 数据模型
│   ├── gui/
│   │   ├── main_window.py       # 主窗口布局与交互编排
│   │   ├── segment_table.py     # 分段脚本编辑表格（增删行、时间校验）
│   │   └── voice_selector.py    # 语言过滤 + 音色下拉 + 语速/音调
│   ├── core/
│   │   ├── tts_provider.py      # TTSProvider 抽象基类
│   │   ├── edge_tts_provider.py # edge-tts 实现（含长文本分块）
│   │   ├── script_parser.py     # .srt / [mm:ss] 解析与导出
│   │   ├── audio_timeline.py    # 按 start_time 铺放音频（补静音/截断）
│   │   └── video_processor.py   # ffmpeg 封装：探测、混音、导出、进度、取消
│   └── utils/
│       ├── ffmpeg_locator.py    # 定位系统 ffmpeg / 一键下载静态构建
│       ├── async_worker.py      # QThread 封装 + 取消信号
│       └── settings.py          # QSettings 配置持久化
├── assets/
├── tests/
│   ├── test_script_parser.py
│   ├── test_audio_timeline.py
│   └── test_video_processor.py
├── docs/
│   ├── PRODUCT.md
│   ├── ARCHITECTURE.md
│   └── VERIFICATION.md
├── AGENTS.md
├── feature_list.json
├── agent-progress.md
├── session-handoff.md
├── init.sh
├── requirements.txt
└── README.md
```

---

## 4. 核心数据模型

```python
# src/models.py
from dataclasses import dataclass, field

@dataclass
class Segment:
    text: str                    # 该段旁白文本
    start_time: float            # 在视频中的起始秒数
    audio_path: str | None = None    # 合成后的音频文件路径（缓存）
    duration: float | None = None    # 合成后的实际时长
    subtitle_end_time: float | None = None  # SRT 字幕结束秒数；仅用于字幕保存/导出

@dataclass
class Project:
    video_path: str
    segments: list[Segment] = field(default_factory=list)
    voice: str = ""                  # edge-tts 音色 ID
    rate: str = "+0%"                # 语速
    pitch: str = "+0Hz"              # 音调
    mix_mode: str = "replace"        # "replace" | "overlay"
    original_volume: float = 0.2     # overlay 模式下原声音量
```

**约束**：数据模型为纯数据容器，不含 I/O、不含 ffmpeg 调用、不含 PyQt 类型。

`duration` 只表示 TTS 合成后的实际音频时长，供音频时间轴计算使用；`subtitle_end_time`
只表示从 SRT 导入或在表格中编辑得到的字幕显示结束时间，供 SRT 导出及 F15 内嵌字幕使用。两者不得复用、
互相覆盖或相互推导。

---

## 5. 模块契约

### `core/tts_provider.py`
```python
class TTSProvider(ABC):
    @abstractmethod
    def list_voices(self, language: str | None = None) -> list[Voice]: ...
    @abstractmethod
    def synthesize(self, text: str, voice: str, rate: str, pitch: str,
                   out_path: str) -> float:
        """合成音频写入 out_path，返回实际时长（秒）。"""
```
- 新增 TTS 引擎 = **新增一个文件**，不得修改调用方。
- `edge_tts_provider.py` 负责：音色列表缓存、按语言过滤、长文本按句分块合成后拼接、网络失败重试。

### `core/script_parser.py`
- `parse_srt(text) -> list[Segment]`
- `parse_simple(text) -> list[Segment]`（`[mm:ss] 文本` 与 `[hh:mm:ss]`）
- `to_srt(segments) -> str`
- `parse_srt` 将 SRT 的结束时间写入 `Segment.subtitle_end_time`，不得写入 `duration`。
- `to_srt` 优先使用 `subtitle_end_time`；对手工创建且未设置该字段的分段，才按既有回退规则推导合法显示结束时间。
- 校验：时间格式非法、结束时间不晚于起始时间、起始时间非递增、字幕显示区间重叠 → 抛出带行号的明确异常。
- **纯函数，无 I/O，无网络**（可直接单测）。

### `gui/segment_table.py` 与 `gui/main_window.py`
- 主界面提供 `.srt` 导入和独立 `.srt` 导出入口；导入成功后将分段写入编辑表格。
- 分段表格展示并允许编辑字幕起始时间、结束时间和文本；手工创建的分段可没有字幕结束时间。
- 导入会替换当前表格时，若已有用户编辑内容必须先确认；解析失败、用户取消或确认拒绝时不得改变当前表格及已生成配音。
- 编辑文本或起始时间后，现有配音视为过期，不能作为新分段的试听或导出结果；仅编辑字幕结束时间不影响已生成音频。

### `core/audio_timeline.py`
- `build_timeline(segments, video_duration) -> TimelinePlan`
- 职责：按 `start_time` 铺放各段音频；间隙补静音；超出 `video_duration` 的部分**截断**（D2）；产出单一完整音轨。
- 必须能在**不实际生成音频**的情况下计算并返回布局与是否溢出（供 UI 提前提示 A5）。

### `core/video_processor.py`
- `probe(path) -> VideoInfo`（时长、分辨率、是否含音轨、编码参数）
- `export(project, timeline, out_path, on_progress, cancel_token)`
- 编码策略：`-c:v copy -c:a aac`；仅当容器/编码不兼容才回退 `-c:v libx264`，且必须通过回调通知上层（触发 UI 提示）。
- 混音：`replace` 丢弃原音轨；`overlay` 用 `amix` + `volume=original_volume`。
- 进度：解析 ffmpeg stderr 的 `time=` 字段。
- 取消：终止子进程并清理临时文件。

### F15 视频内嵌字幕的扩展契约（待实现）

- 保持现有 `export` 调用方默认行为不变；字幕选项必须显式启用。字幕数据取自当前表格快照，
  不得仅依赖 `_synthesized_segments`：未生成配音及只编辑字幕结束时间的场景同样必须正确。
- 字幕时间与文本规范化复用 `core/script_parser.py` 的 F14 规则；GUI 只采集选项和显示错误，
  不复制结束时间推导、时间校验或 ffmpeg 参数算法。
- GUI 区分「配音与字幕一起导出」与「仅添加字幕、保留原声」。前者复用既有配音时间轴和
  replace/overlay 参数；后者不要求配音结果、音色或 TTS 调用，不构造虚假配音/静音轨来绕过前置条件。
- ffmpeg 的字幕输入、临时文件、显式流映射与封装均由 `core/video_processor.py` 负责，
  路径仍通过 `utils/ffmpeg_locator.py` 获取。默认 MP4 使用 `-c:s mov_text`，新增一条字幕轨并设置
  default disposition；不得使用字幕烧录滤镜，不因添加字幕切换视频编码策略。
- 仅添加字幕时保留原音轨内容；兼容 MP4 时优先复制音轨，不兼容时可转为 AAC，不能借此静默
  替换、混音或丢弃原声。没有原音轨时输出仍无音轨。
- 字幕不能延长视频输出或导致视频提前结束；时间边界处理不得回写修改当前表格或独立 SRT。
- 导出复用 `WorkerThread` 的进度、取消、失败提示和临时文件清理机制；禁止静默丢弃字幕并报告成功。
- 不增加 QSettings 持久化键、字幕样式编辑器或应用内视频字幕预览。播放器负责字幕渲染；
  default disposition 仅是标记，不是“播放器一定自动显示”的保证。

### `utils/ffmpeg_locator.py`
- 查找顺序：应用目录 `bin/` → `PATH` → 用户配置路径。
- 缺失时提供下载 LGPL 静态构建的能力，下载后**必须执行 `ffmpeg -version` 校验**才算成功。

### `utils/async_worker.py`
- `QThread` 封装，桥接 edge-tts 的 asyncio 调用与 ffmpeg 子进程。
- 提供 `progress`/`finished`/`error` 信号与 `cancel()`。
- **不得包含业务逻辑**，只做线程与信号桥接。

---

## 6. 错误处理约定
- core 层抛出领域异常（`FFmpegNotFoundError`、`TTSNetworkError`、`ScriptParseError`、`NoAudioStreamError` 等），定义在各自模块内或 `core/errors.py`。
- GUI 层负责将异常翻译为用户可读的中文提示，**core 层不得直接弹窗**。

---

## 7. 测试策略
- `tests/test_script_parser.py`：纯单测，覆盖合法/非法格式、重叠、边界时间。
- `tests/test_audio_timeline.py`：纯单测，覆盖补静音、截断、段落乱序、零段。
- `tests/test_video_processor.py`：命令行参数构造断言（不真跑 ffmpeg）+ 标记 `@pytest.mark.integration` 的端到端用例（需要真实 ffmpeg 与样例视频）。
- **GUI 不做自动化测试**，通过 `docs/VERIFICATION.md` 中的人工检查清单验证。
