#!/usr/bin/env bash
# init.sh — 恢复并校验 attach-voice 开发环境
#
# 用法:
#   bash init.sh            # 恢复环境并校验
#   bash init.sh --check    # 只校验，不安装
#
# 幂等：可重复运行。
# Windows 请在 Git Bash 或 WSL 中运行。

set -uo pipefail

CHECK_ONLY=0
[[ "${1:-}" == "--check" ]] && CHECK_ONLY=1

cd "$(dirname "$0")"

PASS=0
FAIL=0
WARN=0

ok()   { echo "  [ OK ]   $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL]   $*"; FAIL=$((FAIL+1)); }
warn() { echo "  [WARN]   $*"; WARN=$((WARN+1)); }
hdr()  { echo ""; echo "=== $* ==="; }

# ---------------------------------------------------------------- Python
hdr "1. Python 运行时"

# 本项目是 Windows 桌面应用（PyQt6 + PyInstaller）。在 WSL 中运行时，
# Linux 侧解释器无法用于打包与 GUI 验证，需要 Windows 侧的 python.exe。
IS_WSL=0
grep -qi microsoft /proc/version 2>/dev/null && IS_WSL=1
if [[ $IS_WSL -eq 1 ]]; then
  warn "检测到 WSL。PyQt6 GUI 与 PyInstaller 打包必须在 Windows 侧执行；将优先选用 python.exe。"
fi

# 候选解释器：优先 Windows 侧，其次 POSIX 侧
PY=""
PY_FALLBACK=""
for c in python.exe py.exe python python3 py; do
  command -v "$c" >/dev/null 2>&1 || continue
  [[ -z "$PY_FALLBACK" ]] && PY_FALLBACK="$c"
  if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done

if [[ -z "$PY" && -z "$PY_FALLBACK" ]]; then
  bad "未找到 Python。请安装 Python 3.11+ 并加入 PATH。"
  echo ""; echo "环境未就绪，终止。"; exit 1
fi

if [[ -z "$PY" ]]; then
  PY="$PY_FALLBACK"
  PY_VER="$($PY -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null | tr -d '\r' || echo "unknown")"
  bad "可用的 Python 版本过低：$PY ($PY_VER)，要求 >= 3.11"
else
  PY_VER="$($PY -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null | tr -d '\r')"
  ok "Python $PY_VER (>= 3.11) — $(command -v "$PY")"
fi

# ---------------------------------------------------------------- venv
hdr "2. 虚拟环境"

VENV_DIR=".venv"
if [[ -d "$VENV_DIR" ]]; then
  ok "虚拟环境已存在: $VENV_DIR"
elif [[ $CHECK_ONLY -eq 1 ]]; then
  bad "虚拟环境不存在（--check 模式不创建）"
else
  echo "  创建虚拟环境 ..."
  if $PY -m venv "$VENV_DIR"; then ok "已创建 $VENV_DIR"; else bad "创建虚拟环境失败"; fi
fi

# 定位 venv 内的解释器（Windows: Scripts, POSIX: bin）
VPY=""
for p in "$VENV_DIR/Scripts/python.exe" "$VENV_DIR/Scripts/python" "$VENV_DIR/bin/python"; do
  [[ -x "$p" ]] && { VPY="$p"; break; }
done
if [[ -n "$VPY" ]]; then ok "venv 解释器: $VPY"; else warn "未找到 venv 解释器，回退使用系统 $PY"; VPY="$PY"; fi

# ---------------------------------------------------------------- deps
hdr "3. Python 依赖"

if [[ -f requirements.txt ]]; then
  if [[ $CHECK_ONLY -eq 0 ]]; then
    echo "  安装 requirements.txt ..."
    if "$VPY" -m pip install --upgrade pip -q && "$VPY" -m pip install -r requirements.txt -q; then
      ok "依赖安装完成"
    else
      bad "依赖安装失败"
    fi
  fi
  # 逐项校验（模块名 与 pip 包名可能不同）
  for mod in PyQt6 edge_tts pytest; do
    if "$VPY" -c "import $mod" >/dev/null 2>&1; then ok "import $mod"; else bad "缺少模块: $mod"; fi
  done
  if "$VPY" -c "import PyQt6.QtMultimedia" >/dev/null 2>&1; then
    ok "import PyQt6.QtMultimedia（试听功能依赖）"
  else
    bad "缺少 PyQt6.QtMultimedia —— 试听功能将不可用"
  fi
else
  warn "requirements.txt 尚不存在（F01 未完成）—— 跳过依赖安装"
fi

# ---------------------------------------------------------------- ffmpeg
hdr "4. ffmpeg / ffprobe"

check_bin() {
  local name="$1"
  local path=""
  if [[ -x "bin/$name.exe" ]]; then path="bin/$name.exe"
  elif [[ -x "bin/$name" ]]; then path="bin/$name"
  elif command -v "$name" >/dev/null 2>&1; then path="$(command -v "$name")"
  fi
  if [[ -z "$path" ]]; then
    warn "$name 未找到 —— 应用运行时会引导一键下载 LGPL 静态构建（决策 D3）"
    return 1
  fi
  local ver
  ver="$("$path" -version 2>&1 | head -n1)"
  if [[ -n "$ver" ]]; then ok "$name: $path — $ver"; else bad "$name 存在但 -version 校验失败: $path"; fi
}

check_bin ffmpeg
check_bin ffprobe

# ---------------------------------------------------------------- network
hdr "5. 网络（edge-tts 依赖）"

if command -v curl >/dev/null 2>&1; then
  if curl -s -o /dev/null -m 8 -w '%{http_code}' https://speech.platform.bing.com 2>/dev/null | grep -qE '^[234]'; then
    ok "可访问微软语音服务端点"
  else
    warn "无法访问微软语音服务端点 —— 离线环境下 TTS 相关验证会失败"
  fi
else
  warn "未找到 curl，跳过网络检查"
fi

# ---------------------------------------------------------------- harness
hdr "6. Harness 工作区完整性"

for f in AGENTS.md plan.md docs/PRODUCT.md docs/ARCHITECTURE.md feature_list.json agent-progress.md session-handoff.md; do
  if [[ -f "$f" ]]; then ok "存在: $f"; else bad "缺失: $f"; fi
done

if [[ -f feature_list.json ]]; then
  if "$VPY" -c "import json,sys; d=json.load(open('feature_list.json',encoding='utf-8')); ids=[f['id'] for f in d['features']]; assert len(ids)==len(set(ids)); allowed=set(d['status_values']); assert all(f['status'] in allowed for f in d['features']); byid={f['id']:f for f in d['features']}; assert all(dep in byid for f in d['features'] for dep in f['depends_on']), 'bad dependency id'" 2>/dev/null; then
    ok "feature_list.json 结构合法（ID 唯一、状态合法、依赖可解析）"
  else
    bad "feature_list.json 校验失败"
  fi
fi

# ---------------------------------------------------------------- tests
hdr "7. 测试套件（若已存在）"

if [[ -d tests ]] && compgen -G "tests/test_*.py" >/dev/null 2>&1; then
  if "$VPY" -m pytest tests/ -q --collect-only >/dev/null 2>&1; then
    ok "pytest 可正常收集用例"
  else
    bad "pytest 收集用例失败"
  fi
else
  warn "尚无测试文件 —— 属预期（实现尚未开始）"
fi

# ---------------------------------------------------------------- summary
hdr "汇总"
echo "  通过: $PASS   警告: $WARN   失败: $FAIL"
echo ""

if [[ $FAIL -gt 0 ]]; then
  echo "环境未就绪：请先解决上面 [FAIL] 项。"
  exit 1
fi

echo "环境就绪。"
echo ""
echo "下一步："
echo "  1. 阅读 AGENTS.md → docs/PRODUCT.md → docs/ARCHITECTURE.md"
echo "  2. 阅读 session-handoff.md 了解上次进度"
echo "  3. 从 feature_list.json 选取依赖已满足的最小编号 pending feature"
echo ""
echo "  提醒：feature 只有在其全部 verification 条目被实际执行并通过后，才能标记为 verified。"
exit 0
