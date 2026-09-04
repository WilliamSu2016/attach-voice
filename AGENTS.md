# AGENTS.md

> **必读顺序**：本文件 → `docs/PRODUCT.md` → `docs/ARCHITECTURE.md` → `feature_list.json` → `session-handoff.md`
> 在写任何一行应用代码之前，必须完整读完 `docs/PRODUCT.md` 与 `docs/ARCHITECTURE.md`。
> 不读这两份文件就开始实现，属于流程违规。

---

## 1. 项目目的

**attach-voice** 是一款 Windows 桌面应用：让用户为**已有的本地视频**添加 AI 生成的旁白配音。

- 用户提供分段脚本（文本 + 起始时间）→ TTS 合成语音 → 按时间轴混入原视频 → 导出新视频
- 本地处理，视频不上传
- 技术栈：Python 3.11+ / PyQt6 / edge-tts / ffmpeg / PyInstaller

**三条不可动摇的产品红线**（详见 `docs/PRODUCT.md` 决策表）：
1. **画面绝不改动**（D2）：配音短则补静音，长则截断，绝不改变视频画面时长。
2. **不重编码视频**（D4）：`-c:v copy -c:a aac`，仅在容器不兼容时回退并明确提示用户。
3. **TTS 必须走抽象层**（D5）：任何代码不得直接 import `edge_tts`，只能通过 `TTSProvider`。

---

## 2. 仓库结构

```
attach-voice/
├── AGENTS.md              # 本文件：agent 工作契约
├── plan.md                # 高层实现计划（唯一事实来源的上游，勿随意改写）
├── plan-review.md         # 计划评审记录（历史，只读）
├── feature_list.json      # 机器可读特性清单 + 验证标准（agent 的主工作队列）
├── agent-progress.md      # 进度日志：每次状态变更与验证证据（追加写）
├── session-handoff.md     # 跨会话交接快照（每次会话结束前更新）
├── init.sh                # 恢复并校验开发环境
├── requirements.txt
├── README.md
├── docs/
│   ├── PRODUCT.md         # 产品需求（稳定层，WHAT/WHY）
│   ├── ARCHITECTURE.md    # 技术架构与边界（HOW）
│   └── VERIFICATION.md    # 验证执行手册与人工检查清单
├── src/
│   ├── __main__.py        # 入口：python -m src
│   ├── models.py          # Segment / Project
│   ├── gui/               # PyQt6 表现层
│   ├── core/              # 领域逻辑（禁止 import PyQt6）
│   └── utils/             # 基础设施
├── tests/
├── scripts/               # 验证脚本（verify_export.py 等）
└── assets/
```

---

## 3. 开发命令

```bash
# 环境恢复与校验（幂等，可重复运行）
bash init.sh

# 仅校验、不安装（不创建 venv）
bash init.sh --check

# 激活虚拟环境
source .venv/Scripts/activate      # Git Bash / Windows
.\.venv\Scripts\Activate.ps1       # PowerShell

# 运行应用（必须以包方式运行）
python -m src

# 单元测试
python -m pytest tests/ -q

# 单个模块测试
python -m pytest tests/test_script_parser.py -q

# 覆盖率
python -m pytest tests/ -q --cov=src --cov-report=term-missing

# 集成测试（需真实 ffmpeg + 样例视频）
python -m pytest tests/ -q -m integration

# 端到端验收（PRODUCT A1-A5）
python scripts/verify_export.py --all

# 打包
pyinstaller attach-voice.spec --noconfirm
```

> **注意**：本项目开发机为 Windows，`bash` 解析为 WSL。
> `init.sh` 已做适配：在 WSL 下会自动优先选用 Windows 侧的 `python.exe`（当前为 `C:\Python311`），
> 因为 PyQt6 GUI 与 PyInstaller 打包**必须在 Windows 侧执行**，不能用 WSL 的 Linux 解释器。
> 除 `init.sh` 外，其余开发命令请在 **PowerShell** 中运行，并用 `;` 而非 `&&` 串联。
> `init.sh` 必须保持 **LF 换行**，否则 bash 会报 `$'\r': command not found`。

---

## 4. 架构边界（违反即视为实现失败）

依赖方向严格单向：**`gui → core → utils → models`**

| 规则 | 内容 |
|---|---|
| B1 | `src/core/**` **禁止 `import PyQt6`**。核心逻辑必须可脱离 GUI 单测。 |
| B2 | `src/models.py` 不得 import 任何项目内模块，也不得 import PyQt6。 |
| B3 | 禁止反向依赖：`core` 不得 import `gui`；`utils` 不得 import `core`/`gui`。 |
| B4 | `utils/async_worker.py` 是唯一允许同时接触 `QThread` 与 core 的适配器，且**不含业务逻辑**。 |
| B5 | 只有 `utils/ffmpeg_locator.py` 解析 ffmpeg 路径；`video_processor.py` 必须通过它取路径，**禁止硬编码 `"ffmpeg"`**。 |
| B6 | 只有 `core/video_processor.py` 允许 `subprocess` 调 ffmpeg/ffprobe（`ffmpeg_locator` 可调 `ffmpeg -version` 校验）。 |
| B7 | 只有 `utils/settings.py` 允许使用 `QSettings`。 |
| B8 | GUI 层不得包含业务算法（时间轴计算、脚本解析、混音参数推导），只做编排与展示。 |
| B9 | core 层抛领域异常，**不得弹窗**（禁止 `QMessageBox`）；GUI 层负责翻译为中文提示。 |
| B10 | 任何模块不得直接 `import edge_tts`，只有 `core/edge_tts_provider.py` 例外。 |

---

## 5. 实现规则

1. **一次只做一个 feature**。开始前把该 feature 的 `status` 置为 `in_progress`，且同时只允许一个 `in_progress`。
2. **遵守依赖顺序**。只有当 `depends_on` 中所有 feature 状态为 `verified` 时，才能开始该 feature。
3. **不要跳过验证去做下一个 feature**。
4. **不改上游文档**：`plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md` 是稳定层。
   若实现中发现与它们冲突，**停下来向用户确认**，不要私自改文档或偏离设计。
5. **不擅自替换技术栈**（见 `ARCHITECTURE.md#1`）。不引入 `ffmpeg-python`、`moviepy` 等封装库。
6. **新增依赖必须写进 `requirements.txt` 并固定版本**。
7. **注释克制**：只注释需要解释的地方，不做逐行注释。
8. **范围纪律**：只实现当前 feature 的 `acceptance`。发现的无关问题记录到 `agent-progress.md` 的「待办观察」区，不顺手改。
9. **二期功能一律不做**：ASR/翻译/dubbing、Piper 离线 TTS、波形编辑器、背景音乐轨。
10. 每完成一个 feature 的验证后，**必须**更新 `feature_list.json`、`agent-progress.md`、`session-handoff.md` 三处。

---

## 6. 验证规则（最重要）

### 6.1 状态机

```
pending ──> in_progress ──> implemented ──> verified
                │                              ▲
                └──────────> blocked ──────────┘
```

| 状态 | 含义 |
|---|---|
| `pending` | 未开始 |
| `in_progress` | 正在实现（全局最多一个） |
| `implemented` | **代码已写完，但验证尚未执行 —— 这不是完成状态** |
| `verified` | 该 feature 的**所有** `verification[]` 条目都被**实际执行**并通过，证据已记入 `agent-progress.md` |
| `blocked` | 无法推进，必须填写 `blocked_reason` |

### 6.2 铁律

> **绝不允许因为「代码已经存在」或「看起来是对的」就把 feature 标为 `verified`。**

具体要求：
- **每一条 `verification[].command` 必须真正在终端里执行过**，并记录实际输出摘要与退出码。
- `type: "manual"` 的检查必须真正操作过，并在 `agent-progress.md` 写下观察到的现象（不是复述 pass_condition）。
- **任何一条验证未通过或未执行 → 该 feature 状态最高只能是 `implemented`**。
- 验证失败时：记录失败输出 → 修复 → **重新完整执行全部验证条目**（不是只跑失败的那条）。
- 不得为了让验证通过而放宽 `pass_condition` 或删改 `verification` 条目。若某条验证标准确实不合理，**停下来向用户确认**。
- 不得用 `pytest -k`、`--no-cov`、`|| true` 等方式绕过验证。
- 环境原因无法执行（如缺少 10 分钟 1080p 样例视频）→ 状态置 `blocked`，写明 `blocked_reason`，**不得**标 `verified`。

### 6.3 记录格式（写入 `agent-progress.md`）

```markdown
### F04 脚本解析 — verified @ 2026-09-05T10:22:00+08:00
| # | 类型 | 命令 / 检查 | 结果 |
|---|---|---|---|
| 1 | command | `python -m pytest tests/test_script_parser.py -q` | PASS — 18 passed in 0.42s (exit 0) |
| 2 | command | `... --cov-fail-under=90` | PASS — coverage 94% (exit 0) |
| 3 | command | 依赖检查 | PASS — exit 0 |
备注：非法时间格式用例补充了 `[99:99]` 边界。
```

### 6.4 项目级完成定义
项目只有在 `feature_list.json` 中**全部 13 个 feature 状态为 `verified`**，且 `PRODUCT.md` 的 A1–A5 全部有执行记录时，才算完成。

---

## 7. 会话开始 / 结束流程

**开始时**
1. 读 `AGENTS.md`（本文件）
2. 读 `docs/PRODUCT.md` 与 `docs/ARCHITECTURE.md`（**强制，不可跳过**）
3. 读 `session-handoff.md` 了解上次进度
4. 读 `feature_list.json`，选出「所有 `depends_on` 均为 `verified`」的最小编号 `pending` feature
5. 运行 `bash init.sh` 确认环境可用

**结束时**
1. 更新 `feature_list.json` 状态与 `evidence`
2. 追加 `agent-progress.md` 记录
3. 覆盖更新 `session-handoff.md`
4. 确认没有把未验证的 feature 标成 `verified`
