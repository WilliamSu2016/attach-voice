# session-handoff.md — 跨会话交接快照

> **单一快照文件**：每次会话结束前**整体覆盖更新**本文件（不追加）。
> 历史流水记录在 `agent-progress.md`。

---

## 快照元信息

| 项 | 值 |
|---|---|
| 最后更新 | 2026-09-04T15:10:00+08:00 |
| 会话编号 | S001 |
| 会话目标 | 将 plan.md 转换为 Harness Engineering 工作区（不实现应用代码） |
| 会话结果 | 完成 |

---

## 当前项目状态

**阶段**：`Harness 已就绪，应用实现尚未开始`

| 指标 | 值 |
|---|---|
| 总 feature 数 | 13 |
| verified | 0 |
| implemented（待验证） | 0 |
| in_progress | 0 |
| blocked | 0 |
| pending | 13 |

**已存在的源码**：无。`src/`、`tests/`、`scripts/`、`assets/` 尚未创建 —— 这是正确状态。

---

## 本次会话完成的工作

1. 阅读 `plan.md` 与 `plan-review.md`
2. 创建 `docs/PRODUCT.md` —— 抽取稳定产品需求（定位、决策 D1–D5、用户流程、R1–R13、验收标准 A1–A5、环境要求、风险）
3. 创建 `docs/ARCHITECTURE.md` —— 技术栈、分层与依赖规则 B1–B10、仓库结构、数据模型、模块契约、错误处理、测试策略
4. 创建 `feature_list.json` —— plan.md 的 13 个 Todo 转为 F01–F13，含依赖图、验收条件与可执行验证标准
5. 创建 `AGENTS.md` —— agent 工作契约（目的/结构/命令/边界/实现规则/验证规则/必读顺序）
6. 创建 `init.sh` —— 环境恢复与七类校验（Python、venv、依赖、ffmpeg、网络、Harness 完整性、测试收集）
7. 创建 `agent-progress.md` 与本文件
8. **未修改** `plan.md`、`plan-review.md`；**未编写任何应用源码**（符合本次任务约束）

---

## 下一个会话应该做什么

### 立即执行
```bash
bash init.sh --check   # 预期：仅 venv 相关 FAIL（尚未创建）+ requirements/tests 的 WARN，属正常
bash init.sh           # 创建 .venv 并安装依赖（需先有 requirements.txt，即 F01 完成后）
```

> 环境实测（S001）：Windows 侧 Python 3.11.0 (`C:\Python311`)、WSL 侧 ffmpeg/ffprobe 4.4.2 可用、
> 微软语音服务端点可访问。`bash` 为 WSL，`init.sh` 已自动切到 Windows 侧 `python.exe`。
> **Windows 侧目前未检测到 ffmpeg** —— F06 的集成验证需在 PowerShell 侧确认 ffmpeg 可用（或走 F02 的一键下载）。

### 下一个 feature：**F01 — 项目脚手架与数据模型**
- 依赖：无（可立即开始）
- 交付物：`requirements.txt`、`src/__init__.py`、`src/__main__.py`、`src/models.py`、三个子包的 `__init__.py`、`tests/__init__.py`
- 关键约束：
  - `models.py` 字段必须与 `docs/ARCHITECTURE.md#4` 完全一致
  - `models.py` 不得 import PyQt6 或任何项目内模块（规则 B2）
  - `python -m src --version` 必须可运行
- 完成后**必须实际执行** `feature_list.json` 中 F01 的 3 条验证命令，记录退出码与输出，再更新状态

### 就绪队列（依赖已满足的 pending feature）
- **F01**（无依赖）→ 完成并 verified 后解锁 F02、F03、F04

---

## 需要用户决策的悬留问题

| # | 问题 | 影响的 feature | 建议 |
|---|---|---|---|
| Q1 | 是否提供 10 分钟 1080p 样例视频用于 A3 性能验收？未提供则 F06/F11 的对应验证只能置 `blocked` | F06, F11 | 请用户放置于 `assets/samples/`，或授权用 ffmpeg 生成合成测试视频 |
| Q2 | edge-tts 需联网。若开发环境断网，F03 的音色列表与合成验证无法执行 | F03 | 断网时置 `blocked`，不得标 `verified` |
| Q3 | `scripts/verify_export.py` 等验证脚本本身是 F11 的交付物，但 F05/F06 的验证条目已引用它 | F05, F06 | 建议在 F05/F06 实现时随手创建对应脚本的最小可用版本，F11 时再补全 `--all` |

---

## 给下个会话的重要提醒

1. **先读 `AGENTS.md` → `docs/PRODUCT.md` → `docs/ARCHITECTURE.md`**，再动手。
2. **`implemented` ≠ 完成。** 只有全部 `verification[]` 条目被**真正执行并通过**、证据写入 `agent-progress.md` 后，才能置为 `verified`。
3. 三条产品红线：画面绝不改动（D2）、不重编码视频（D4）、TTS 必须走抽象层（D5）。
4. 不要修改 `plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`；有冲突请向用户确认。
5. 同时只允许一个 feature 处于 `in_progress`。
