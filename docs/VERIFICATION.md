# 验证执行手册（F11）

本文档汇总 PRODUCT.md §5「验收标准 A1-A5」的执行方式、对应脚本/测试，以及当前的执行状态与证据。
详细的历史执行记录（含每次运行的实际输出）见 `agent-progress.md`。

## 总览

| ID | 标准 | 验证方式 | 状态 |
|---|---|---|---|
| A1 | 输出视频画面时长与原视频完全一致（误差 < 0.05s） | `scripts/verify_export.py --all`（`run_a1`） | ✅ 已执行通过 |
| A2 | 各段配音实际起始时间与设定值一致（偏差 < 0.2s） | `scripts/verify_export.py --all`（`run_a2`，内部复用 `scripts/verify_timeline_offsets.py`） | ✅ 已执行通过 |
| A3 | 10 分钟 1080p 导出 < 15s，且未重编码视频流 | `scripts/verify_export.py --all`（`run_a3`） | ✅ 已执行通过 |
| A4 | 输出文件可被 VLC / Windows Media Player / 浏览器实际打开，正常播放、音画同步 | 人工确认（见下文「A4 执行说明」） | ✅ 已由用户人工确认（2026-09-05） |
| A5 | 配音超出视频时长时，导出前出现明确提示，且实际行为为截断而非改画面 | GUI 提示：`scripts/verify_error_handling.py`（场景 3，F10）；截断行为：`scripts/verify_export.py --all`（`run_a5`） | ✅ 已执行通过 |

## 如何复现

```powershell
# 单元测试全绿（含 integration 标记用例）
python -m pytest tests/ -q

# A1/A2/A3/A5 端到端断言
python scripts\verify_export.py --all

# F10 的 GUI 提示回归（A5 的提示部分 + 其他错误处理场景）
python scripts\verify_error_handling.py
```

## A4 执行说明（证据来源：用户人工确认 + agent 半自动化补充）

PRODUCT.md 对 A4 的度量方式明确写的是「VLC / Windows Media Player / 浏览器实际打开」——这是一条要求
人用眼睛看、用耳朵听来确认"播放流畅、音画同步"的人工验收项。**agent 不具备听觉能力，无法代为完成
这类主观判断**；因此本条的最终通过依据是**用户于 2026-09-05T19:15 的人工确认**，而非 agent 的执行
证据。下面把两部分证据如实分开列出，避免后续被误读为"agent 已亲自验证"。

### 一、用户人工确认（本条的通过依据）

用户于 2026-09-05T19:15 确认导出文件在播放器中播放正常，并据此指示将 F11 置为 `verified`。

### 二、agent 侧的半自动化补充证据（辅助，不能替代上一条）

1. **环境探测**：本机已安装 Windows Media Player（`C:\Program Files (x86)\Windows Media Player\wmplayer.exe`）
   与 Google Chrome（`C:\Program Files\Google\Chrome\Application\chrome.exe`），**未安装 VLC**。
2. **真实导出样例**：用 `overlay` 混音模式导出了一个包含真实 TTS 配音（"这是用于人工播放验证的测试
   配音，请确认音画同步。"）与合成测试画面（`testsrc`, 10s, 960x540）的文件
   `assets/samples/a4_manual_check_output.mp4`（H.264 + AAC，MP4 容器）。
3. **Windows Media Player**：用 `Start-Process` 启动 wmplayer.exe 打开该文件，等待数秒后用
   `System.Drawing` 截屏两次（间隔 3 秒）。截图证据显示：播放器已加载并显示 `testsrc` 画面、
   播放进度条从 00:02 前进、暂停/播放按钮处于"播放中"状态、底部彩色渐变条在两帧间发生了明显位移
   （证明画面确实在随时间推进渲染，而非卡死或黑屏）。
4. **Chrome**：用 `Start-Process` 启动 chrome.exe 以 `file://` 方式打开该文件，Chrome 内置的原生视频
   播放器自动播放；截图证据显示标签页出现"扬声器"图标（表示该标签正在输出音频），画面正常渲染；
   等待播放完整个 10 秒时长后再次截图，标签页音频图标消失、画面停在末帧——与"播放完整个文件、无崩溃
   、无卡死"的预期行为一致。
5. **agent 未能覆盖的部分**：
   - **VLC 未安装**，agent 未能在 VLC 中打开验证；
   - agent **没有听觉能力**，无法判断"音画同步"是否符合人的听感体验；截图只能证明画面在渲染、
     音频轨道存在且被播放器识别为"正在输出"，不能证明音画时间轴对齐的精确度（A2 的能量起点检测
     在数值上间接佐证了时间轴对齐，但 A4 要求的是播放器层面的主观确认，二者不能互相替代）。

### 三、若需补强记录

本文件目前只记录了"用户确认播放正常"这一结论。如需更完整的人工观察记录（用户在 VLC / WMP / Chrome
中具体看到与听到的现象），应由**用户本人**补充到 `agent-progress.md`；**agent 不得代为编造观察现象**。
