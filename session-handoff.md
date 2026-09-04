# session-handoff.md — 跨会话交接快照

> **单一快照文件**：每次会话结束前**整体覆盖更新**本文件（不追加）。
> 历史流水记录在 `agent-progress.md`。

---

## 快照元信息

| 项 | 值 |
|---|---|
| 最后更新 | 2026-09-04T16:35:00+08:00 |
| 会话编号 | S002 |
| 会话目标 | 实现并验证 F01（项目脚手架与数据模型），只做这一个 feature |
| 会话结果 | 完成 — F01 已 `verified` |

---

## 当前项目状态

**阶段**：`应用实现已开始，骨架就绪`

| 指标 | 值 |
|---|---|
| 总 feature 数 | 13 |
| verified | 1（F01） |
| implemented（待验证） | 0 |
| in_progress | 0 |
| blocked | 0 |
| pending | 12 |

**已存在的源码**：
```
requirements.txt          PyQt6/edge-tts/pytest/pytest-cov 固定版本
src/__init__.py           __version__ = "0.1.0"
src/__main__.py           argparse 入口，支持 --version
src/models.py             Segment / Project（与 ARCHITECTURE.md#4 一致）
src/gui/__init__.py       空包（占位）
src/core/__init__.py      空包（占位）
src/utils/__init__.py     空包（占位）
tests/__init__.py         空包（尚无测试用例）
scripts/  assets/         空目录
```

**环境**：`.venv` 已创建并安装依赖（Windows 侧 Python 3.11.0）。
`bash init.sh --check` = 通过 18 / 警告 2 / 失败 0。

---

## 本次会话完成的工作

1. 依序读完 `AGENTS.md` → `docs/PRODUCT.md` → `docs/ARCHITECTURE.md` → `feature_list.json` → `session-handoff.md`
2. 运行 `bash init.sh`，创建 `.venv`（14 OK / 3 WARN / 0 FAIL）
3. F01 置 `in_progress`，创建 `requirements.txt` 与 `src/`、`tests/`、`scripts/`、`assets/` 骨架
4. `pip install -r requirements.txt` 成功（PyQt6 6.9.1、edge-tts 7.2.8、pytest 8.4.2、pytest-cov 6.3.0）
5. **实际执行** F01 的全部 3 条 verification，均 exit 0 → 置 `verified`，证据写入 `agent-progress.md`
6. 未触碰其他 feature；未修改 `plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`

---

## 下一个会话应该做什么

### 立即执行
```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest tests/ -q      # 目前 no tests ran（预期）
```

### 下一个 feature：**F02 — ffmpeg 定位与一键获取**
- 依赖：F01（已 `verified`）→ 可立即开始
- 交付物：`src/utils/ffmpeg_locator.py`、`tests/test_ffmpeg_locator.py`
- 关键约束：
  - 查找顺序：应用目录 `bin/` → `PATH` → 用户配置路径
  - `find_ffmpeg()` 返回路径或 `None`，**绝不抛出未捕获异常**
  - 下载后必须 `ffmpeg -version` 校验才算成功（D3 / R13）
  - 规则 B5：只有本模块解析 ffmpeg 路径；规则 B3：`utils` 不得 import `core`/`gui`
- **风险**：验证条目 3 是 `manual`（移除 PATH 中 ffmpeg 后确认一键下载引导）。
  Windows 侧当前本就没有 ffmpeg（见观察 O3），有利于该手动验证；但下载需访问外网 ffmpeg 静态构建站点，
  若被网络策略拦截 → 该条置 `blocked`，**不得**标 `verified`。

### 就绪队列（依赖已满足的 pending feature）
- **F02**（依赖 F01 ✅）
- **F03**（依赖 F01 ✅）— 需联网
- **F04**（依赖 F01 ✅）— 纯单测，最易 verified

> 按 AGENTS.md「最小编号优先」，下一个应为 **F02**。

---

## 需要用户决策的悬留问题

| # | 问题 | 影响的 feature | 建议 |
|---|---|---|---|
| Q1 | 是否提供 10 分钟 1080p 样例视频用于 A3 性能验收？未提供则对应验证只能置 `blocked` | F06, F11 | 请放置于 `assets/samples/`，或授权用 ffmpeg 生成合成测试视频 |
| Q2 | edge-tts 需联网。若开发环境断网，F03 的音色列表与合成验证无法执行 | F03 | 断网时置 `blocked`，不得标 `verified` |
| Q3 | `scripts/verify_export.py` 等脚本是 F11 交付物，但 F03/F05/F06 的验证条目已引用 | F03, F05, F06 | 在对应 feature 实现时先建最小可用版本，F11 补全 `--all` |
| Q4 | F02 的一键下载需访问外网 ffmpeg 构建站点，是否允许？ | F02 | 若被拦截，需用户指定本地 ffmpeg 路径或授权代理 |

---

## 给下个会话的重要提醒

1. **先读 `AGENTS.md` → `docs/PRODUCT.md` → `docs/ARCHITECTURE.md`**，再动手。
2. **`implemented` ≠ 完成。** 只有全部 `verification[]` 条目被**真正执行并通过**、证据写入 `agent-progress.md` 后，才能置 `verified`。
3. 三条产品红线：画面绝不改动（D2）、不重编码视频（D4）、TTS 必须走抽象层（D5）。
4. 不要修改 `plan.md`、`plan-review.md`、`docs/PRODUCT.md`、`docs/ARCHITECTURE.md`；有冲突请向用户确认。
5. 同时只允许一个 feature 处于 `in_progress`。
6. 所有 Python 命令请用 `.\.venv\Scripts\python.exe`（PowerShell），`init.sh` 仍走 WSL bash。
