# session-handoff.md — 跨会话交接快照

> **单一快照文件**：每次会话结束前**整体覆盖更新**本文件（不追加）。
> 历史流水记录在 `agent-progress.md`。

---

## 快照元信息

| 项 | 值 |
|---|---|
| 最后更新 | 2026-09-05T22:16:00+08:00 |
| 会话编号 | S016 |
| 会话目标 | （承 S014/S015）修复缺陷"重新生成配音后试听仍放旧配音"；补齐 PRODUCT.md R6「每段独立试听」（逐行试听按钮 + 全局按时间轴渲染播放）；（本段）按用户指示，取消「试听」「试听已生成配音」「仅导出音频」必须先选视频的前提，仅「导出视频」保留该前提 |
| 会话结果 | **本期范围内完成，四处 GUI 交互问题已处理**。F11 `verified`；F12/F13 `deferred`（用户指示）。缺陷修复 1：重新生成配音后试听放旧配音（O16/O17/O18）。缺陷修复 2：逐段独立试听按钮 + 全局按序连续播放。缺陷修复 3：全局试听改为按时间轴渲染播放。缺陷修复 4（本段）：`_build_timeline_plan()` 新增 `require_video` 参数，未选视频时改用最后一段配音结束时间作为时间轴总长；`_on_export_audio_only_clicked`/`_on_preview_clicked` 均传 `require_video=False`，仅 `_build_project_and_plan`（供「导出视频」使用）仍传 `True`。`tests/test_segment_preview_controls.py` 更新为 8 条用例，`scripts/verify_segment_preview_controls.py` 新增"清空视频后仍能正常渲染播放"场景，共 10/10 PASS。全量测试 **158 passed**。**未改变任何 feature 的状态**，均为对已 `verified` 的 F07 试听/导出交互的缺陷修复/需求补齐。新增流程教训 **O19**（负向对照严禁用 `git checkout --` 撤销，见下）。 |

---

## 当前项目状态

**阶段**：`本期范围内完成`（`AGENTS.md` §6.4）

| 指标 | 值 |
|---|---|
| 总 feature 数 | 13 |
| verified | **11**（F01–F11） |
| implemented（待验证） | 0 |
| in_progress | 0 |
| blocked | 0 |
| **deferred** | **2（F12 PyInstaller 打包、F13 文档）— 用户主动挂起** |
| pending | 0 |

**完成判定**：按 `AGENTS.md` §6.4，存在用户挂起的 `deferred` feature 时，项目状态记为「本期范围内完成」——
全部**非 `deferred`** 的 feature（F01–F11）均为 `verified`，且 PRODUCT.md 的 A1–A5 全部有执行记录。
F12/F13 **不计入完成**，也不得被当作已完成对待。

### 挂起清单（deferred）

| Feature | 标题 | `deferred_reason` 摘要 |
|---|---|---|
| F12 | PyInstaller 打包 | 用户于 2026-09-05T19:15 指示本期不做。**非受阻**：F11 已 verified，依赖满足，代码侧无已知障碍。恢复由用户决定改回 `pending`。 |
| F13 | 文档（README） | 用户于 2026-09-05T19:15 指示本期不做。**非受阻**。其 `depends_on` 为 F12，F12 亦已挂起，恢复时需与 F12 一并由用户决定。 |

**环境**：`.venv` 可用（Windows 侧 Python 3.11.0）。PyQt6 6.9.1。`bin/` 下 `ffmpeg.exe`/`ffprobe.exe` 可用。
本机媒体播放器：**已安装** Windows Media Player 与 Google Chrome，**未安装** VLC。

---

## PRODUCT.md 验收标准 A1–A5 终态

| ID | 状态 | 证据来源 |
|---|---|---|
| A1 | ✅ PASS | agent 实际执行 — `verify_export.py --all`，时长误差 0.000s（< 0.05s） |
| A2 | ✅ PASS | agent 实际执行 — `verify_export.py --all`（内部复用 `verify_timeline_offsets.py`），最大偏差 0.180s（< 0.2s） |
| A3 | ✅ PASS | agent 实际执行 — `verify_export.py --all`，600s/1920x1080 样例导出耗时 2.20s（< 15s），编码与源一致（未重编码） |
| A4 | ✅ PASS | **用户人工确认**（2026-09-05T19:15）。agent 不具备听觉能力、本机未装 VLC，**未亲自观察播放效果**；agent 侧仅有 WMP+Chrome 真实启动截图的半自动化补充证据。详见 `docs/VERIFICATION.md`「A4 执行说明」 |
| A5 | ✅ PASS | agent 实际执行 — GUI 提示部分见 `verify_error_handling.py` 场景 3；截断行为见 `verify_export.py --all` 的 `run_a5`（输出时长 = 源时长，误差 0.000s） |

---

## 本次会话完成的工作

1. **F11 实现与验证**：
   - 扩展 `scripts/verify_export.py`：新增 `run_a2()`（复用 `verify_timeline_offsets.main()` 的真实合成 + 能量起点检测）与 `run_a5()`（核心层构造超长配音 overflow 场景，断言导出后视频时长仍等于源时长）；`--all` 由「A1+A3」扩展为「A1+A2+A3+A5」，与 verification 条目 2 的 pass_condition 对齐。
   - 新建 `docs/VERIFICATION.md`：汇总 A1–A5 的验证方式、复现命令与证据状态。
   - 执行验证项 1：`python -m pytest tests/ -q` → **145 passed**，无 skip。
   - 执行验证项 2：`python scripts/verify_export.py --all` → **exit 0**，A1/A2/A3/A5 全部 PASS。
   - 验证项 3（A4）：agent 真实导出 `assets/samples/a4_manual_check_output.mp4`（overlay + 真实 TTS 配音），用 `Start-Process` 真实启动 WMP 与 Chrome 打开并截图取证；但因 VLC 未安装 + agent 无听觉能力，**未能亲自完成**该条，一度如实置 F11 为 `blocked`。
2. **新增 `deferred` 流程状态（用户指示）**：见下节「流程规范变更」。
3. **状态调整（用户指示，2026-09-05T19:15）**：
   - F11 `blocked` → `verified`：验证项 3 由**用户人工确认**通过。已在 `feature_list.json` 的 `evidence`、`agent-progress.md` 的记录与台账、`docs/VERIFICATION.md` 中**如实区分**"agent 实际执行"与"用户人工确认"两类证据来源，未把用户确认包装成 agent 的执行证据。
   - F12、F13 `pending` → `deferred`，并填写 `deferred_reason`（明确记录是谁、何时、基于什么理由决定的，以及"非受阻"的性质）。
4. 未修改 `plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`。
5. **缺陷修复（用户报告，会话后段）**：见下节「本次会话修复的缺陷」。

### 本次会话修复的缺陷：重新生成配音后试听仍播放旧配音

> **不是新 feature，也没有改变任何 feature 的状态。** 涉及代码归属 F07/F08，二者仍为 `verified`。

- **现象**：生成配音 → 修改文本 → 再次生成配音 → 点击「试听」，播放的仍是第一次的配音。
- **根因（两层叠加）**：(a) `_generate_narration()` 固定写到 `out_dir/seg{i}.mp3`，第二次生成**原地覆盖**，两次 `Segment.audio_path` 字符串完全相同；(b) `QMediaPlayer.setSource()` 对"与当前相同的 URL"跳过重新加载，继续播放已缓冲的旧音频，且旧文件句柄未释放。
- **修复**（`src/gui/main_window.py` 两处）：`_generate_narration()` 每次生成写入独立的 `out_dir/run-<uuid4[:8]>/` 子目录；`_on_preview_clicked()` 设新源前先 `stop()` + `setSource(QUrl())` 清空（纵深防御）。
- **新增**：`tests/test_main_window_preview.py`（4 条回归单测；**修复前实测 3 failed 复现缺陷**，修复后 4 passed）、`scripts/verify_preview_refresh.py`（真实 `MainWindow` + 真实 `QTest` 点击 + **真实 edge-tts 合成** + 真实 `QMediaPlayer`，12 项断言，分「磁盘层」与「播放器缓存层」两层判定）。
- **验证结果**：`pytest tests/ -q` → **149 passed**（原 145 + 新增 4）；`python scripts/verify_preview_refresh.py` → **12/12 PASS, exit 0**。关键数据：第一次 `run-ac17ef79/seg0.mp3` = 2.448s，第二次 `run-5be6145a/seg0.mp3` = 22.128s，第一次的文件仍存在未被覆盖，第二次试听后**播放器自身报告** `duration()` = 22.128s。
- **负向对照（本次最有价值的一步）**：`git stash` 临时回退修复后重跑脚本 → exit 1，2 项 FAIL，恰好各自命中两层根因（路径未变；`player.duration()` 报 **2.448s** 旧配音）。该实验还暴露出脚本最初的 ffprobe 磁盘侧断言**在缺陷版本上照样 PASS**（因原地覆盖，磁盘内容早已是新的），据此才补入了 `QMediaPlayer.duration()` 这条真正有区分力的断言 —— 详见观察 **O18**。
- **新增观察**：**O16**（缺陷类型化教训 —— 多轮触发场景必须至少验证两轮且第二轮输入不同）、**O17**（`_tmp_dir` 无 `closeEvent` 清理，按范围纪律未顺手改，已写明建议做法）、**O18**（修复缺陷后必须做负向对照；断言要打在症状发生的那一层）。

### 本次会话补齐的需求：逐段独立试听按钮 + 全局按钮改为按序连续播放

> **不是新 feature，是对已 `verified` 的 F07 中「试听」交互的需求补齐，未改变任何 feature 状态。**
> 用户发现 `_on_preview_clicked()` 一直只播放 `_synthesized_segments[0]`（永远第一段），未真正
> 实现 `docs/PRODUCT.md` R6「每段独立试听」；确认方案后按用户指示实现。

- **改动**：`src/gui/segment_table.py` 每行「删除」按钮左侧新增「试听」按钮，发出
  `preview_row_requested(row)` 信号（按控件身份重新定位当前行号，删除行后仍正确）。
  `src/gui/main_window.py` 新增 `_preview_queue`/`_preview_queue_pos` + `_on_media_status_changed`
  （监听 `mediaStatusChanged`，`EndOfMedia` 时推进队列播放下一段）；`_on_preview_clicked()`
  （全局按钮）改为把全部已生成段落按序入队连续播放；新增 `_on_segment_preview_requested(row)`
  （单行「试听」，清空队列后只播该行，避免与连续播放混淆）。
- **新增**：`tests/test_segment_preview_controls.py`（7 条单测：按钮布局、行号跟随删除更新、
  单行试听清空队列、全局按序建队、`EndOfMedia` 正确推进、末段播完队列清空不越界、连续播放中途
  切单行试听不误续播）；`scripts/verify_segment_preview_controls.py`（真实 `MainWindow` + 真实
  `QTest` 点击 + **真实 edge-tts 合成 3 段时长两两可辨（2.18s/8.09s/21.82s）** + 真实
  `QMediaPlayer` **真实播放并等待其自然触发 `EndOfMedia`**，非手动模拟）。
- **验证结果**：`pytest tests/test_segment_preview_controls.py -q` → 7 passed；
  `pytest tests/ -q`（全量）→ **156 passed**（149 + 新增 7）；
  `python scripts/verify_segment_preview_controls.py` → **9/9 PASS, exit 0**（点第 3 行试听只播
  段 C；点全局按钮从段 A 开始，真实等待 2.18s/8.09s/21.82s 后依次自然切到段 B、段 C、清空队列）。
- **负向对照**（沿用 O18 方法论）：`git stash` 临时回退本次改动后重跑脚本 → exit 1，2 项 FAIL
  （无「试听」按钮列；`_preview_queue` 属性不存在），恢复后 `git diff --stat` 确认改动完整。

### 本次会话补齐的需求：全局「试听已生成配音」改为按时间轴渲染播放

> **不是新 feature，是对已 `verified` 的 F07 中「全局试听」交互的进一步需求补齐，未改变任何
> feature 状态。** 用户指出上一轮"按序连续播放"仍不对：那是把各段原始文件掐头去尾首尾拼接，
> 没有按 `start_time` 摆放、没有段间静音间隔，不像导出结果那样"按时间轴播放"；并要求确认
> 「仅导出音频」也按时间轴、含全部分段。

- **调查结论**：「仅导出音频」经代码走查确认**本来就正确**（复用 `build_timeline`+
  `export_audio_only`，与导出视频共用同一套时间轴规划/渲染路径），本次未改动该按钮代码，只
  补充了一条更强的多段+间隔集成测试。「全局试听」才是需要修复的地方。
- **改动**：`src/gui/main_window.py` 新增 `_build_timeline_plan(action_label)`（从
  `_build_project_and_plan()` 抽出公共的"校验视频/分段就绪 + `build_timeline()` +
  `AudioTimelineError` 处理"逻辑，供导出与试听共用）；移除 `_preview_queue`/
  `_preview_queue_pos`/`_on_media_status_changed`；`_on_preview_clicked()` 改为构建
  `TimelinePlan` 后用 `WorkerThread` 异步调用 `_export_audio_only_task` 渲染到临时文件（复用
  「仅导出音频」的核心渲染函数，保证与导出结果字节级一致），渲染完成后播放该文件；
  `_on_segment_preview_requested(row)` 简化（不再需要清空队列）。
- **测试重写**：`tests/test_segment_preview_controls.py`（原 3 条依赖 `_preview_queue` 的用例
  已失效，重写为 7 条新用例：按钮布局/行号不变，单行试听 2 条，全局试听新增 3 条——缺视频/缺
  已合成分段报错、通过 monkeypatch 替身断言真正构建了覆盖全时长且按顺序含全部分段的
  `TimelinePlan` 并在渲染完成后播放渲染产物）；`scripts/verify_segment_preview_controls.py`
  重写为：真实合成 40 秒 lavfi 视频 + 3 段真实 edge-tts 配音（`start_time`=0/15/30，段间必有
  真实静音间隔），真实点击全局按钮等待真实 `WorkerThread`（内部真实调用 ffmpeg）渲染完成，
  用真实 ffprobe 校验渲染产物总时长 ≈ 视频时长（证明含静音间隔，非简单拼接）。
- **新增**：`tests/test_video_processor.py::test_export_audio_only_multi_segment_with_gaps_places_segments_at_start_time_real_ffmpeg`
  （`@pytest.mark.integration`）：2 段真实配音 + 3 秒真实静音间隔，用真实 ffmpeg
  `silencedetect` 滤镜确认输出文件里存在落在"段 0 结束附近"的静音区间，佐证「仅导出音频」
  已正确按时间轴含全部分段（本次未改动该按钮代码）。
- **验证结果**：`pytest tests/test_segment_preview_controls.py -q` → 7 passed；
  `pytest tests/test_video_processor.py -q -m integration -k multi_segment` → 1 passed；
  `pytest tests/ -q`（全量，含全部 integration 用例）→ **157 passed**（156 + 新增 1）；
  `python scripts/verify_segment_preview_controls.py` → **8/8 PASS, exit 0**（视频探测时长
  40.0s；点第 2 行「试听」立即指向该段原始文件；点全局按钮等待真实 ffmpeg 渲染完成后，
  播放器 source 是渲染出的临时文件（不等于任何一段原始文件），真实 ffprobe 探测其时长为
  40.00s = 视频时长）。
- **负向对照**（沿用 O18 方法论）：`git stash push -- src/gui/main_window.py` 临时回退改动
  后重跑脚本 → exit 1，3 项 FAIL（点行「试听」未指向原始文件；全局试听播放的仍是段 0 原始
  文件而非渲染产物；渲染"时长"实际就是段 0 时长 2.18s ≠ 视频时长 40s），`git stash pop` 恢复
  后确认改动完整、全量回归仍 157 passed。

### 本次会话补齐的需求：「试听」「试听已生成配音」「仅导出音频」取消"必须先选视频"的前提

> **不是新 feature，是对已 `verified` 的 F07 中导出/试听交互的进一步需求补齐，未改变任何
> feature 状态。** 用户明确指出：这三个只处理配音本身的操作不应该要求先选视频，只有需要把
> 配音混入真实视频画面的「导出视频」才必须先选视频。

- **改动**：`_build_timeline_plan(action_label, *, require_video=True)` 新增 `require_video`
  参数——已选视频时仍以其时长为准；未选视频且 `require_video=False` 时改用**最后一段配音的
  结束时间**作为时间轴总长（不再要求先选视频）；未选视频且 `require_video=True`（仅供「导出
  视频」使用）维持原有报错。抽出 `_confirm_truncation_if_needed(plan)` 供「导出视频」「仅
  导出音频」共用截断确认弹窗逻辑。`_on_export_audio_only_clicked()` 改为直接调用
  `_build_timeline_plan(..., require_video=False)`，不再依赖会强制要求视频的
  `_build_project_and_plan()`；`_on_preview_clicked()`（全局试听）同样传 `require_video=False`。
- **测试更新**：`tests/test_segment_preview_controls.py` 移除过时的"未选视频应报错"用例，
  新增"未选视频不报错、`TimelinePlan.total_duration` = 最后一段配音结束时间"用例（全局试听 +
  仅导出音频各一条，共 8 条）；`scripts/verify_segment_preview_controls.py` 追加"清空已选
  视频后重新点击全局试听"场景，真实验证不报错、真实渲染完成、渲染时长变为最后一段配音结束
  时间（32.83s）而非原视频时长（40s）。
- **验证结果**：`pytest tests/test_segment_preview_controls.py -q` → 8 passed；
  `pytest tests/ -q`（全量）→ **158 passed**（157 + 新增 1）；
  `python scripts/verify_segment_preview_controls.py` → **10/10 PASS, exit 0**。
- **负向对照**：把源码中 `require_video=False` 全部临时替换为 `True` 后重跑单测 → 2 项
  FAIL（全局试听/仅导出音频在未选视频时均误报"请先选择视频"），确认脚本有真实区分力。
- **本次流程事故与教训（新增观察 O19）**：负向对照恢复阶段一度误用
  `git checkout -- src/gui/main_window.py`，结果撤销了该文件**本次会话此前的全部未提交改动**
  （而不只是刚才的临时替换），一度丢失了缺陷修复 1/2/3 的全部实现。靠 `git fsck --unreachable`
  从此前 `git stash pop` 留下的悬挂提交中 `git show <sha>:path > file` 找回，最终确认恢复正确
  （全量回归仍 158 passed），未造成实际损失，但过程凭运气。**结论：临时改动源码做负向对照，
  事后严禁用 `git checkout --`/`git restore` 撤销**（那是整文件级操作，会抹掉所有未提交改动），
  应改用「改动前 `git diff > backup.patch`，验证完 `git apply backup.patch` 精确恢复」或
  「与临时改动完全对称的逆操作原地改回」。详见 O19。

### 流程规范变更：新增 `deferred` 状态

用于表达"用户主动决定暂不做"的挂起场景，与既有 `blocked`（想做但受外部阻碍做不了）严格区分：

- `AGENTS.md` §6.1：状态机图补 `deferred` 分支；状态含义表补 `deferred` 行并把 `blocked` 措辞改精确为"想做但做不了"；新增 **`blocked` vs `deferred` 对照表**（成因 / 是否还想做 / 谁能设置 / 必填字段 / 解除方式）；补铁律"`deferred` 不是逃生舱"。
- `AGENTS.md` §5 规则2：依赖被 `deferred` 时下游 feature 同样不可开始，必须停下来问用户。
- `AGENTS.md` §6.2：不得用 `deferred` 规避实现或验证。
- `AGENTS.md` §6.4：存在 `deferred` 时项目记为「本期范围内完成」，`deferred` 不计入完成。
- `AGENTS.md` §7：会话开始时选 feature 跳过 `deferred`；结束时确认 `blocked_reason`/`deferred_reason` 已填。
- `feature_list.json`：`status_values` 追加 `"deferred"`；`status_rules` 补 `blocked`/`deferred` 详细语义（含"只有用户能决定 deferred"）；`hard_rule` 补充禁止把 `deferred` 当逃生舱；13 个 feature 全部补齐 `deferred_reason` 字段。
- 详见观察 **O15**。

---

## 下一个会话应该做什么

### 结论：**没有可以自主开工的 feature**

- F01–F11 全部 `verified`。
- F12、F13 是 `deferred`（**用户主动挂起**）——按 `AGENTS.md` §6.1、§7，**只有用户可以解除挂起**。
  **不要**因为"就剩两个 feature 了"就自行把它们改回 `pending` 并开工。
- 因此若用户没有新的指示，正确做法是：向用户汇报当前状态（本期范围内完成 + 两个挂起项），
  并询问下一步意图，而不是自行找活干。

### 若用户要求恢复 F12 / F13

1. 由**用户**把对应 feature 的 `status` 由 `deferred` 改回 `pending`（或明确指示 agent 改），并清空/更新 `deferred_reason`。
2. F13 依赖 F12，需按依赖顺序先做 F12。
3. F12 的 verification 含 2 条 manual（运行打包后的 exe 试听、在干净环境运行 exe），
   **注意**：这类"运行 GUI 可执行文件并听声音"的检查与 A4 同属"agent 感官/环境能力之外"的类别
   （见下方提醒 5），提前想清楚证据边界，不要重蹈 F11 的返工。

### 健康检查命令（确认现状未变）
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest tests\ -q                          # 应为 158 passed（含 integration，真实网络+真实 ffmpeg，较慢）
python scripts\verify_export.py --all               # 应为 A1/A2/A3/A5 全部 PASS，exit 0
python scripts\verify_preview_refresh.py            # 试听刷新缺陷回归，应为 12/12 PASS，exit 0（真实 TTS，需联网）
python scripts\verify_segment_preview_controls.py   # 逐段试听 + 全局按时间轴渲染播放 + 无需视频前提，应为 10/10 PASS，exit 0（真实 TTS + 真实 ffmpeg，需联网）
```

---

## 需要用户决策的悬留问题

当前**无阻塞性悬留问题**。F11 的 A4 争议已由用户人工确认关闭；F12/F13 的去留已由用户决定为挂起。

> Q1–Q9 均已解决或关闭，详见 `agent-progress.md`。

---

## 给下个会话的重要提醒

1. **先读 `AGENTS.md` → `docs/PRODUCT.md` → `docs/ARCHITECTURE.md`**，再动手。
2. **`deferred` 只能由用户解除。** F12/F13 当前是用户主动挂起，agent **不得**自行改回 `pending` 开工，也不得自行把别的 feature 标为 `deferred` 来规避工作（`AGENTS.md` §6.1 铁律补充）。
3. **`blocked` 与 `deferred` 不可混用**：`blocked` = 想做但受外部阻碍（agent 可自行判定）；`deferred` = 用户主动决定不做（只有用户能设）。
4. **证据来源必须如实区分。** F11 的 A4 是「用户人工确认」而非「agent 实际执行」，这一区分已写进 `feature_list.json` evidence、`agent-progress.md` 台账与记录、`docs/VERIFICATION.md`。后续引用 A4 时**不要**简化成"已验证通过"而抹掉来源；也**不得**代用户编造具体的播放观察现象。
5. **物理/感官类 manual 验证的处理边界（F11 血泪经验）**：F06–F10 建立的"QTest 模拟真实点击 + 客观指标替代主观判断"模式，只适用于**GUI 内部操作**；**不适用于**需要 agent 不具备的感官能力（听觉）或外部环境（未安装的软件）的验证项。遇到这类项，正确做法是拆分"能自动化的部分"与"不能自动化的部分"分别如实记录，宁可 `blocked` 也不用不完整证据冒充 `verified`，然后请用户确认。**F12 的两条 manual 验证属于同一类别**。
6. 三条产品红线：画面绝不改动（D2）、不重编码视频（D4）、TTS 必须走抽象层（D5）。F01–F11 的所有已执行验证均未发现违反迹象。
7. 不要修改 `plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`；有冲突请向用户确认。
8. 所有 Python 命令请用 `.\.venv\Scripts\python.exe`（PowerShell），`init.sh` 仍走 WSL bash。
9. **规则 B7**：只有 `src/utils/settings.py` 允许使用 `QSettings`。验证脚本里新建 `MainWindow()` 时务必注入指向临时 ini 的 `AppSettings`（参考 `scripts/verify_settings_persistence.py`/`verify_error_handling.py` 的 `make_settings()`），避免污染本机注册表（观察 O13：`verify_gui_flow.py` 仍有此缺口，未修）。
10. **规则 B9（core 不弹窗）**：core 层代码/注释中不要出现字面的 "QMessageBox"，会触发 F10 verification 1 的文本扫描误报。
11. **观察 O12**：GUI 构造阶段为设置控件初始状态而"顺手"调用信号槽处理函数时，留意其中是否耦合了持久化写入，避免初始化时序 bug。
12. **观察 O14**：`scripts/verify_gui_flow.py` 曾两次在真实导出步骤附近卡死（CPU 趋近 0）。若再次运行仍卡死，需认真排查根因而非归为偶发。
13. **观察 O15**：脚本化改写 `feature_list.json` **不要**用 `json.dump(indent=2)`——原文件的 `depends_on`/`requirements`/`files`/`verification` 是紧凑单行风格，标准 dump 会展开成 500+ 行格式噪音 diff。需用保持原风格的自定义序列化器 + round-trip 断言。
14. **观察 O5**（F03 多块分句合成路径未做真实网络端到端联调）、**O11**（F08 TTS 取消粒度限制）、**O7**（PRODUCT R10「仅导出音频」的 feature 归属未明确）仍未关闭，若用户后续扩展范围可一并复核。
15. **观察 O16（重要，缺陷类型化教训）**：凡"同一路径被反复写入 + 交给带缓存的播放/加载组件"的组合都会重演"试听放旧内容"这类缺陷。**新增任何可重复触发的 GUI 操作（重新生成、重新导出、重新加载视频等），验证时必须至少跑两轮且第二轮输入不同，并断言产物确实随输入变化**，而不只断言单轮结果正确。F07/F08 原有验证只覆盖单轮路径，正是因此漏掉了该缺陷。
16. **观察 O17**：`MainWindow` 仍无 `closeEvent` 清理 `self._tmp_dir`（临时目录泄漏，既有问题）。若要修，需在删除前先 `stop()` + `setSource(QUrl())` 释放播放器句柄；**不要**改成"生成前删除上一轮产物"，那会重新引入试听读到已删文件的缺陷。
17. **观察 O18（重要，验证方法论）**：**修复缺陷后必须做「负向对照」**——临时回退修复（`git stash`）重跑验证脚本，确认它**确实会 FAIL**，且 FAIL 的正是对应根因的那一条。本次若不做这一步，就会交付一个漏判"播放器缓存层"的验证脚本：ffprobe 查磁盘文件的断言在缺陷版本上照样 PASS（因为缺陷是原地覆盖，磁盘内容早已是新的）。**断言必须打在症状发生的那一层**（症状在播放器缓存，就得查 `QMediaPlayer.duration()`）。本项目此前的 `verify_*.py` 均未做过负向对照，可信度未经此检验。
18. **`SegmentTable` 列布局已变化**：`_COL_START_TIME=0, _COL_TEXT=1, _COL_PREVIEW=2, _COL_REMOVE=3`（原来删除按钮在列 2，现在列 3；新增的每行「试听」按钮在列 2）。若后续脚本用 `cellWidget(row, 2)` 期望拿到删除按钮，会拿错到试听按钮——写新验证脚本时留意。
19. 各 `scripts/verify_*.py` 会在 `assets/samples/`/临时目录生成样例文件，已被 `.gitignore` 排除。其中 `assets/samples/a4_manual_check_*` 是 A4 人工复核的证据文件，**建议保留**，不要当临时文件清理。
20. **全局「试听已生成配音」按钮已不再是"队列播放"**：`MainWindow` 不再有 `_preview_queue`/`_preview_queue_pos`/`_on_media_status_changed`。全局试听现在会先异步渲染出一个临时时间轴音轨文件（复用 `_export_audio_only_task`），再播放该文件；`_media_player.source()` 点击全局按钮后指向的是**渲染产物路径**，不是任何一段原始配音文件——若后续再写验证脚本，不要照抄旧脚本里"断言 source 依次等于各段 `audio_path`"的写法。逐行「试听」按钮语义未变（仍直接播放该行原始文件）。
21. **「试听」「试听已生成配音」「仅导出音频」不再要求先选视频**，只有「导出视频」需要。`_build_timeline_plan(action_label, *, require_video=True/False)` 是唯一的时间轴构建入口：未选视频且 `require_video=False` 时用**最后一段配音的结束时间**当作时间轴总长。若后续新增依赖时间轴的功能，记得按"是否真的需要视频画面"决定传哪个值，不要一律沿用旧的 `require_video=True` 默认值。
22. **观察 O19（重要，工具使用教训）**：**负向对照时若用直接编辑源码的方式临时改动（而非 `git stash`），事后严禁用 `git checkout --`/`git restore` 撤销**——那是整文件级操作，会连同该文件此前全部未提交改动一并抹掉，不是撤销"最近一次改动"。正确做法：(a) 改动前 `git diff <file> > backup.patch`，验证完 `git apply backup.patch` 精确恢复；(b) 用与临时改动完全对称的逆操作原地改回。本次一度因此丢失 `src/gui/main_window.py` 整个会话的改动，靠 `git fsck --unreachable` 从此前 `stash pop` 留下的悬挂提交中找回，纯属侥幸（若该 stash 已被 gc 清理则无法找回）。
