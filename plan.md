# AI Voiceover for Existing Videos — 桌面应用实现计划（v2，已按 review 修订）

> 本版本根据 `plan-review.md` 的 review 结论及用户确认的取舍重写。
> 主要变更：引入多段脚本时间轴模型、钉死时长策略与编解码策略、抽象 TTS Provider 接口、明确 ffmpeg 分发方式与验收标准。

## 需求确认
- **应用形态**：桌面应用（本地处理，无需上传视频，保护隐私）
- **核心场景**：用户提供脚本文本 → AI 生成配音 → 合成到已有本地视频（script-to-voice）
- **视频输入**：仅本地文件（拖拽 / 文件选择）
- **TTS 方案**：`edge-tts`（免费、无需 API Key、多语言 400+ 音色，需联网）

## 已确认的关键决策
| # | 议题 | 决策 |
|---|---|---|
| D1 | 脚本模型 | **支持多段**：段列表（文本 + 起始时间），支持 .srt 导入 |
| D2 | 音视频时长不匹配 | **画面绝不改动**：配音短则补静音，配音长则截断，导出前弹窗提醒 |
| D3 | ffmpeg 分发 | 优先用系统 ffmpeg；检测不到则引导**一键下载 LGPL 静态构建**到应用目录 |
| D4 | 视频编解码 | `-c:v copy -c:a aac`，仅重编码音轨；容器不兼容时才回退重编码视频 |
| D5 | TTS 架构 | 先定义 `TTSProvider` 抽象基类，edge-tts 为第一个实现（规避非官方接口的单点风险） |

## 技术栈
- **语言**：Python 3.11+
- **GUI**：PyQt6（含 `QtMultimedia` 用于试听，需在打包时显式验证）
- **TTS**：`edge-tts`
- **音视频**：ffmpeg（subprocess 直接调用，便于捕获进度与取消）
- **配置持久化**：`QSettings`（记忆音色、语速、输出目录）
- **打包**：PyInstaller → Windows 可执行程序

## 运行环境与硬件要求

**结论：本方案对硬件要求极低，无需 GPU，普通办公电脑即可流畅运行。**

这是本选型的核心优势 —— 两个最吃资源的环节都被绕开了：
- **语音合成不在本地跑**：edge-tts 是调用微软云端服务，本地零推理负载，不需要显卡、不需要大内存
- **视频不重编码**：D4 决策的 `-c:v copy` 只处理音轨，导出过程基本是磁盘 I/O，与视频分辨率、码率几乎无关

### 最低配置
| 项目 | 要求 | 说明 |
|---|---|---|
| CPU | 任意双核 x64（近 10 年的机器均可） | 仅用于音频编码与 UI |
| 内存 | 4 GB | PyQt6 应用常驻约 200–400 MB |
| 显卡 | **不需要**（集显即可） | 无任何本地 AI 推理 |
| 磁盘 | 500 MB 程序空间 + 约 2 倍源视频大小的临时/输出空间 | 分段音频缓存约 1 MB/分钟，占用极小 |
| 网络 | **必需**，带宽要求低（合成 1 分钟语音约传输 1 MB） | edge-tts 依赖联网 |
| 系统 | Windows 10/11 x64（PyQt6 跨平台，macOS/Linux 亦可运行） | |

### 推荐配置
四核 CPU + 8 GB 内存 + SSD。此配置下 10 分钟 1080p 视频的导出应在 15 秒内完成（即验收标准）。

### 例外情况（唯一会吃 CPU 的场景）
当源视频容器与目标格式不兼容、`-c:v copy` 失败而回退到**视频重编码**时，耗时会从秒级升到分钟级，且与 CPU 核数强相关。
→ 应对：默认输出 `.mp4`（与绝大多数源格式兼容，回退概率极低）；一旦触发回退，UI 必须明确提示"正在重新编码视频，耗时较长"，避免用户误以为卡死。

### 二期离线 TTS 的硬件影响（提前说明）
若后续接入离线 TTS，选型直接决定硬件门槛，这也是计划中选 Piper 而非 Bark/Coqui 的原因：
- **Piper TTS（计划采用）**：轻量 ONNX 模型，纯 CPU 即可达到实时以上速度，内存占用几百 MB，**仍然不需要 GPU**
- Bark / Coqui XTTS（不采用）：需要 6 GB 以上显存的独立显卡，否则 CPU 推理会慢到不可用

## 核心数据模型
```python
@dataclass
class Segment:
    text: str            # 该段旁白文本
    start_time: float    # 在视频中的起始秒数
    audio_path: str|None # 合成后的音频文件路径（缓存）
    duration: float|None # 合成后的实际时长

@dataclass
class Project:
    video_path: str
    segments: list[Segment]
    voice: str              # edge-tts 音色 ID
    rate: str               # 语速，如 "+10%"
    pitch: str              # 音调
    mix_mode: str           # "replace" | "overlay"
    original_volume: float  # overlay 模式下原声音量，默认 0.2
```

## 核心流程（MVP）
1. 选择本地视频文件 → 读取并显示时长、分辨率、是否含音轨
2. 编辑脚本段落表格（增/删/改行：起始时间 + 文本），或导入 .srt / 简单 `[00:15] 文本` 格式
3. 选择语言（先过滤）→ 音色 → 语速 / 音调
4. 「生成配音」：逐段调用 edge-tts 合成，显示进度，可取消；生成后每段可单独试听
5. 时长校验：UI 显示"视频 X 秒 / 配音占用至 Y 秒"，若超出则弹窗提示将被截断
6. 选择混音模式：`替换原音轨` 或 `叠加原音轨`（原声音量可调，默认 20%）
7. 「导出」：ffmpeg 按各段 `start_time` 铺到时间轴上并混音，`-c:v copy` 输出新视频
8. 附加：「仅导出音频」，输出合成后的完整配音轨 mp3

## 项目结构
```
attach-voice/
├── src/
│   ├── __main__.py              # 入口，以 python -m src 运行（避免相对导入问题）
│   ├── models.py                # Segment / Project 数据模型
│   ├── gui/
│   │   ├── main_window.py       # 主窗口布局与交互
│   │   ├── segment_table.py     # 分段脚本编辑表格（增删行、时间校验）
│   │   └── voice_selector.py    # 语言过滤 + 音色下拉 + 语速/音调
│   ├── core/
│   │   ├── tts_provider.py      # TTSProvider 抽象基类
│   │   ├── edge_tts_provider.py # edge-tts 实现（含长文本分块）
│   │   ├── script_parser.py     # .srt / [mm:ss] 格式解析与导出
│   │   ├── audio_timeline.py    # 按 start_time 将各段音频铺成完整音轨（补静音/截断）
│   │   └── video_processor.py   # ffmpeg 封装：探测信息、混音、导出、进度解析、取消
│   └── utils/
│       ├── ffmpeg_locator.py    # 定位系统 ffmpeg / 一键下载静态构建
│       ├── async_worker.py      # QThread 封装 + 取消信号
│       └── settings.py          # QSettings 配置持久化
├── assets/
├── tests/
│   ├── test_script_parser.py
│   ├── test_audio_timeline.py
│   └── test_video_processor.py
├── requirements.txt
└── README.md
```

## 实现步骤（Todos）
1. **项目脚手架**：venv、`requirements.txt`（PyQt6、edge-tts）、`src/` 包布局、`models.py` 数据模型
2. **ffmpeg 定位与获取**：`ffmpeg_locator.py` —— 查 PATH / 应用目录，缺失时引导一键下载 LGPL 静态构建并校验可用
3. **TTS 抽象与实现**：`tts_provider.py` 基类 + `edge_tts_provider.py`（列音色、按语言过滤、长文本分句分块、合成并返回时长）
4. **脚本解析**：`script_parser.py` —— 解析 / 导出 .srt 与 `[mm:ss] 文本` 格式，含时间格式与重叠校验
5. **音频时间轴合成**：`audio_timeline.py` —— 按 `start_time` 铺放各段音频，间隙补静音，超出视频时长部分截断，产出单一完整音轨
6. **视频处理**：`video_processor.py` —— ffprobe 探测信息；replace / overlay 两种混音；`-c:v copy -c:a aac` 导出；解析 ffmpeg 进度；支持终止子进程
7. **GUI 主界面**：`main_window.py` + `segment_table.py` + `voice_selector.py` —— 视频选择、分段表格、音色参数、试听、时长对比提示、混音模式与原声音量、进度条、导出/仅导出音频
8. **异步串联与取消**：`async_worker.py` 用 QThread 桥接 edge-tts 异步调用与 ffmpeg 子进程，实现进度回传与任务取消
9. **配置持久化**：`settings.py` 用 QSettings 记忆音色、语速、输出目录、混音模式
10. **错误处理与用户提示**：ffmpeg 缺失、视频无音轨、格式不支持、TTS 网络失败与限流重试、段落时间越界
11. **测试与验收**：单元测试（解析、时间轴计算）+ 端到端验证，需满足下方验收标准
12. **打包**：PyInstaller 生成 Windows 可执行程序，重点验证 QtMultimedia 与 ffmpeg 路径在打包后可用
13. **文档**：README（安装、ffmpeg 说明、使用步骤、许可声明）

## 验收标准（可度量）
- 输出视频**画面时长与原视频完全一致**（误差 < 0.05s）
- 各段配音在输出中的实际起始时间与设定值偏差 **< 0.2s**
- 10 分钟 1080p 视频导出耗时 **< 15s**（验证 `-c:v copy` 生效，未发生视频重编码）
- 输出文件可被 VLC / Windows Media Player / 浏览器正常播放
- 配音总时长超出视频时，导出前必须出现明确提示，且实际行为为截断而非改动画面

## 风险与注意事项
- **edge-tts 是非官方逆向接口**，存在接口变更或 IP 限流风险 → 已通过 `TTSProvider` 抽象隔离；需实现失败重试与友好报错；二期可接入 Piper TTS 实现完全离线
- **ffmpeg 许可**：不在安装包中内嵌，改为运行时引导用户下载 LGPL 静态构建，README 中注明许可归属
- **QtMultimedia 打包风险**：PyInstaller 易遗漏多媒体插件，需在打包步骤单独验证试听功能
- **视频重编码回退**：`-c:v copy` 失败时会回退重编码，耗时从秒级升到分钟级 → 默认输出 mp4 降低触发概率，触发时 UI 必须明确提示
- **长文本**：edge-tts 单次请求过长可能超时/截断，需按句子分块后拼接
- **范围外（二期）**：语音转录 + 翻译 + 多语言配音（dubbing）、离线 TTS（Piper）、波形可视化时间轴编辑器、背景音乐轨
