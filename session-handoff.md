# session-handoff.md — 跨会话交接快照

> **单一快照文件**：每次会话结束前**整体覆盖更新**本文件（不追加）。
> 历史流水记录在 `agent-progress.md`。

---

## 快照元信息

| 项 | 值 |
|---|---|
| 最后更新 | 2026-09-05T19:20:00+08:00 |
| 会话编号 | S012 |
| 会话目标 | 实现并验证 F11；会话后段按用户指示新增 `deferred` 流程状态，并调整 F11/F12/F13 状态 |
| 会话结果 | **本期范围内完成**。F11 已 `verified`（验证项 1、2 由 agent 实际执行通过；验证项 3 即 PRODUCT A4 由**用户人工确认**）。F12、F13 经用户指示置为 `deferred`（主动挂起，非受阻）。流程层面新增了 `deferred` 状态并写入 `AGENTS.md` 与 `feature_list.json`。 |

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
4. 未触碰任何应用代码（`src/`、`tests/` 本次会话后段无改动）；未修改 `plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`。

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
python -m pytest tests\ -q            # 应为 145 passed（含 integration，真实网络+真实 ffmpeg，较慢）
python scripts\verify_export.py --all # 应为 A1/A2/A3/A5 全部 PASS，exit 0
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
15. 各 `scripts/verify_*.py` 会在 `assets/samples/`/临时目录生成样例文件，已被 `.gitignore` 排除。其中 `assets/samples/a4_manual_check_*` 是 A4 人工复核的证据文件，**建议保留**，不要当临时文件清理。
