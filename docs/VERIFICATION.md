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

# 缺陷回归：改文本后重新生成配音，试听必须播放新配音（详见「缺陷回归验证」一节）
python scripts\verify_preview_refresh.py

# 逐段独立试听 + 全局按序连续播放（详见下方「逐段试听」一节）
python scripts\verify_segment_preview_controls.py
```

## 缺陷回归验证（非 A1–A5，但同样必须保持通过）

| 脚本 | 覆盖的缺陷 | 判定方式 |
|---|---|---|
| `scripts/verify_preview_refresh.py` | 生成配音 → 修改文本 → 再次生成 → 点击试听，播放的仍是**第一次**的配音 | 真实 `MainWindow` + 真实 `QTest.mouseClick` + **真实 edge-tts 合成** + 真实 `QMediaPlayer`，共 12 项断言，全绿则 exit 0。分两层判定（见下方注意事项）。 |
| `tests/test_main_window_preview.py` | 同上（纯单测层，不需网络） | `_FakeTTSProvider` 把待合成文本写进输出文件，从而可按**内容**区分两轮产物；断言路径唯一、内容为新文本、多段路径跨轮不冲突。 |

> **注意事项（由负向对照实验得出，勿简化）**：判定"试听放的是新配音还是旧配音"必须分两层，缺一会漏判。
> - **第 1 层（磁盘）**：两次生成必须产出**不同路径**，且第一次的文件仍存在（未被原地覆盖）。
> - **第 2 层（播放器缓存）**：必须断言 `QMediaPlayer.duration()`——**播放器自己报告的**时长，
>   而**不能**用 ffprobe 去查 `source()` 指向的磁盘文件。因为缺陷版本是**原地覆盖**，磁盘上的内容
>   早已是新配音，ffprobe 无论如何都读到新时长；真正的用户可感症状是"播放器没重新加载、仍在放
>   缓冲里的旧音频"，只有 `player.duration()` 能暴露。负向对照实测：缺陷版本 `player.duration()`
>   报 **2.448s**（旧配音），而新配音实为 **22.128s**；修复版本报 22.128s。
>
> 根因备忘：`_generate_narration()` 曾固定写到 `out_dir/seg{i}.mp3`（第二次原地覆盖，两轮 `audio_path` 相同），
> 而 `QMediaPlayer.setSource()` 对相同 URL 会跳过重新加载。现已改为每轮写入独立的 `run-<uuid8>/` 子目录，
> 并在试听前 `stop()` + `setSource(QUrl())` 清空旧源。详见 `agent-progress.md` 观察 **O16**。

## 逐段独立试听 + 全局按时间轴渲染播放（PRODUCT.md R6 需求补齐）

`docs/PRODUCT.md` R6「每段独立试听」原先未被真正实现——`_on_preview_clicked()` 只会播放
`_synthesized_segments[0]`（永远第一段）。第一轮修复后曾改为"全局按钮按分段顺序连续播放全部"，
但用户进一步指出：连续播放是把各段原始文件掐头去尾首尾拼接，没有按 `start_time` 摆放、没有
段间静音间隔，不像导出结果那样"按时间轴播放"。现已改为：**全局「试听已生成配音」按钮与
「仅导出音频」共用同一套 `build_timeline()` + `export_audio_only()` 渲染路径**，先异步渲染出
一个完整的时间轴音轨临时文件（含段间静音、超长截断，与导出结果字节级一致），再播放该文件；
逐行「试听」按钮维持只播该行原始文件、不做时间轴渲染。

**进一步需求补齐**：用户随后指出「试听」「试听已生成配音」「仅导出音频」都不应以选择视频为
前提——只有「导出视频」才需要真实视频文件用于混音。`_build_timeline_plan()` 新增
`require_video` 参数：未选视频且 `require_video=False` 时，改用**最后一段配音的结束时间**
作为时间轴总长（不再要求先选视频），仍会因段落互相重叠而截断（与视频无关的截断逻辑不变）。

| 脚本 | 覆盖内容 | 判定方式 |
|---|---|---|
| `scripts/verify_segment_preview_controls.py` | 每行「试听」只播该行原始文件；全局按钮触发真实 ffmpeg 渲染出的时间轴音轨（覆盖全部分段、含段间静音）后播放该渲染文件；**清空已选视频后**全局试听仍能正常渲染播放，渲染时长改为最后一段配音结束时间 | 真实 `MainWindow` + 真实 `QTest.mouseClick` + 真实合成 40 秒 lavfi 视频 + **真实 edge-tts 合成 3 段配音（`start_time` 分别为 0/15/30，段间必有真实静音间隔）** + 真实 `WorkerThread`（内部真实调用 ffmpeg）+ 真实 ffprobe 校验渲染产物总时长；选视频场景应 ≈ 视频时长（40s），清空视频后场景应 ≈ 最后一段配音结束时间（约 32.8s）。10 项断言，全绿则 exit 0。 |
| `tests/test_segment_preview_controls.py` | 同上（快速单测层，用 monkeypatch 替身 `WorkerThread`/`_start_worker` 避免真实 ffmpeg 调用） | 8 条用例：按钮布局、删除行后行号更新正确、单行试听只播该行、全局试听/仅导出音频**未选视频时不报错**且 `TimelinePlan.total_duration` 等于最后一段配音结束时间、缺已合成分段时仍报错、全局试听构建的 `TimelinePlan` 覆盖全时长且按顺序含全部分段并在"渲染完成"回调后播放渲染产物路径。 |
| `tests/test_video_processor.py::test_export_audio_only_multi_segment_with_gaps_places_segments_at_start_time_real_ffmpeg`（`@pytest.mark.integration`） | 佐证「仅导出音频」本身已正确按时间轴含全部分段（该按钮代码本次未改动，仅补强证据） | 2 段真实 edge-tts 配音、`start_time` 之间人为留 3 秒真实静音间隔，渲染后用真实 ffmpeg `silencedetect` 滤镜确认输出文件里存在落在"段 0 结束附近"的静音区间，且总时长与 `video_duration` 一致。 |

> 已按 O18 做过负向对照：`git stash push -- src/gui/main_window.py` 临时回退改动后重跑
> `verify_segment_preview_controls.py` → exit 1，3 项 FAIL（点行「试听」未指向原始文件；全局
> 试听播放的仍是段 0 原始文件而非渲染产物；渲染"时长"实际就是段 0 时长 2.18s ≠ 视频时长 40s），
> `git stash pop` 恢复后确认改动完整、全量回归仍 157 passed。

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
