#!/usr/bin/env python
"""scripts/verify_tts_duration.py

F03 验证条目 4：真实调用 EdgeTTSProvider.synthesize() 合成一段已知文本，
断言其返回的时长与 ffprobe 实测该音频文件的时长误差 < 0.05s。

需要联网（调用微软语音服务）且需要 Windows/PATH 中可用的 ffprobe（用于独立复核，
本脚本本身不属于 src/core/video_processor.py，因此不受「只有 video_processor.py
可 subprocess 调 ffmpeg/ffprobe」规则约束——该规则限定的是应用 src/ 包内的业务代码，
本脚本是仓库根 scripts/ 下的独立验证工具，属 O1 观察中提到的「先建最小可用版本」）。

退出码 0 = 通过；非 0 = 失败。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# 允许脚本以 `python scripts/verify_tts_duration.py` 直接运行（无需 -m）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.edge_tts_provider import EdgeTTSProvider  # noqa: E402

TEST_TEXT = "这是一段用于校验合成时长的测试文本，包含若干句子。第二句在这里。第三句用于增加长度。"
TEST_VOICE = "zh-CN-XiaoxiaoNeural"
MAX_DURATION_DIFF_SECONDS = 0.05

# 与 src/utils/ffmpeg_locator.py 相同的 LGPL 静态构建来源，仅用于本脚本临时获取 ffprobe
# 做独立复核（Windows 侧 PATH 目前无 ffmpeg/ffprobe，见 agent-progress.md 观察 O3）
_FFPROBE_DOWNLOAD_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _download_ffprobe(dest_dir: Path) -> str:
    archive_path = dest_dir / "ffmpeg.zip"
    urllib.request.urlretrieve(_FFPROBE_DOWNLOAD_URL, archive_path)
    with zipfile.ZipFile(archive_path) as zf:
        member = next(
            (n for n in zf.namelist() if n.replace("\\", "/").endswith("/bin/ffprobe.exe")),
            None,
        )
        if member is None:
            raise RuntimeError("下载的压缩包中未找到 ffprobe.exe")
        target = dest_dir / "ffprobe.exe"
        with zf.open(member) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return str(target)


def _locate_ffprobe(tmp_dir: Path) -> str:
    which_path = shutil.which("ffprobe")
    if which_path:
        return which_path
    print("PATH 中未找到 ffprobe，临时下载 LGPL 静态构建用于独立复核……")
    return _download_ffprobe(tmp_dir)


def ffprobe_duration(ffprobe_path: str, path: str) -> float:
    result = subprocess.run(
        [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=15,
        check=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        out_path = str(tmp_path / "verify_tts_duration.mp3")

        provider = EdgeTTSProvider()
        reported_duration = provider.synthesize(
            TEST_TEXT, TEST_VOICE, "+0%", "+0Hz", out_path
        )

        ffprobe_path = _locate_ffprobe(tmp_path)
        actual_duration = ffprobe_duration(ffprobe_path, out_path)
        diff = abs(reported_duration - actual_duration)

        print(f"synthesize() 返回时长: {reported_duration:.3f}s")
        print(f"ffprobe 实测时长:      {actual_duration:.3f}s")
        print(f"误差:                  {diff:.3f}s（阈值 {MAX_DURATION_DIFF_SECONDS}s）")

        if diff >= MAX_DURATION_DIFF_SECONDS:
            print("FAIL — 误差超出阈值")
            return 1

        print("PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
