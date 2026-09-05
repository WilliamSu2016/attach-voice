"""ffmpeg 可执行文件定位与一键获取。

依据 ARCHITECTURE.md#5：只有本模块负责解析 ffmpeg 可执行文件路径（规则 B5）；
`core/video_processor.py` 必须通过本模块取路径，禁止硬编码 "ffmpeg"。
本模块仅依赖标准库，符合规则 B3（utils 不得 import core/gui）。
"""
from __future__ import annotations

import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

# LGPL 静态构建下载地址（Windows x64，gyan.dev essentials build，符合 PRODUCT.md D3 许可要求）
FFMPEG_DOWNLOAD_URL_WINDOWS = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

_EXE_SUFFIX = ".exe" if platform.system() == "Windows" else ""
_FFMPEG_EXE_NAME = f"ffmpeg{_EXE_SUFFIX}"
_FFPROBE_EXE_NAME = f"ffprobe{_EXE_SUFFIX}"


class FFmpegNotFoundError(RuntimeError):
    """找不到可用的 ffmpeg 可执行文件，或下载/校验失败。"""

    def __init__(self, message: Optional[str] = None):
        super().__init__(
            message
            or (
                "未找到 ffmpeg 可执行文件。请安装 ffmpeg 并加入 PATH，"
                "或使用一键下载功能获取 LGPL 静态构建。"
            )
        )


def _app_dir() -> Path:
    """应用根目录：PyInstaller 打包后为可执行文件所在目录，源码运行时为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 本文件路径 src/utils/ffmpeg_locator.py -> 项目根目录
    return Path(__file__).resolve().parent.parent.parent


def _app_bin_dir() -> Path:
    return _app_dir() / "bin"


def verify_ffmpeg(path) -> bool:
    """执行 `<path> -version` 校验该可执行文件确实可用。绝不抛出未捕获异常。"""
    try:
        result = subprocess.run(
            [str(path), "-version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def find_ffmpeg(user_configured_path: Optional[str] = None) -> Optional[str]:
    """按顺序查找 ffmpeg：应用目录 bin/ → PATH → 用户配置路径。

    `user_configured_path` 由上层（`utils/settings.py` 持久化配置）传入；
    本模块不直接依赖 Qt 的配置持久化类（规则 B7 仅允许 settings.py 使用该类）。
    返回可执行文件路径，找不到或发生任何异常时返回 None，绝不抛出未捕获异常。
    """
    try:
        candidates = [_app_bin_dir() / _FFMPEG_EXE_NAME]

        which_path = shutil.which("ffmpeg")
        if which_path:
            candidates.append(Path(which_path))

        if user_configured_path:
            candidates.append(Path(user_configured_path))

        for candidate in candidates:
            try:
                if candidate.is_file() and verify_ffmpeg(candidate):
                    return str(candidate)
            except OSError:
                continue
    except Exception:
        return None
    return None


def download_ffmpeg(progress_callback: Optional[Callable[[float], None]] = None) -> str:
    """下载 LGPL 静态构建到应用目录 bin/，校验通过后返回可执行文件路径。

    下载完成后必须执行 `ffmpeg -version` 校验（verify_ffmpeg），校验失败视为下载失败，
    抛出 FFmpegNotFoundError（PRODUCT.md R13 / D3）。
    """
    if platform.system() != "Windows":
        raise FFmpegNotFoundError(
            "一键下载目前仅支持 Windows 平台，请手动安装 ffmpeg 并加入 PATH。"
        )

    bin_dir = _app_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = Path(tmp_dir) / "ffmpeg.zip"

        def _report(block_num: int, block_size: int, total_size: int) -> None:
            if progress_callback and total_size > 0:
                progress_callback(min(1.0, block_num * block_size / total_size))

        try:
            urllib.request.urlretrieve(
                FFMPEG_DOWNLOAD_URL_WINDOWS, archive_path, reporthook=_report
            )
        except OSError as exc:
            raise FFmpegNotFoundError(
                f"下载 ffmpeg 失败：{exc}。请检查网络连接，或手动安装 ffmpeg 并加入 PATH。"
            ) from exc

        try:
            with zipfile.ZipFile(archive_path) as zf:
                exe_member = next(
                    (
                        name
                        for name in zf.namelist()
                        if name.replace("\\", "/").endswith(f"/bin/{_FFMPEG_EXE_NAME}")
                    ),
                    None,
                )
                if exe_member is None:
                    raise FFmpegNotFoundError(
                        "下载的压缩包中未找到 ffmpeg 可执行文件，下载失败。"
                    )
                target_path = bin_dir / _FFMPEG_EXE_NAME
                with zf.open(exe_member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)

                # ffprobe 与 ffmpeg 同一压缩包分发（core/video_processor.py 探测视频信息需要），
                # 一并解压到同一 bin/ 目录，与 ffmpeg 保持同目录分发的约定（B5：仍只有本模块
                # 负责解析/落地这些可执行文件，video_processor.py 只是从 ffmpeg 路径推导同目录
                # 的 ffprobe 路径，不做任何下载或 PATH 搜索）。
                ffprobe_member = next(
                    (
                        name
                        for name in zf.namelist()
                        if name.replace("\\", "/").endswith(f"/bin/{_FFPROBE_EXE_NAME}")
                    ),
                    None,
                )
                if ffprobe_member is not None:
                    ffprobe_target_path = bin_dir / _FFPROBE_EXE_NAME
                    with zf.open(ffprobe_member) as src, open(ffprobe_target_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    ffprobe_target_path.chmod(
                        ffprobe_target_path.stat().st_mode | stat.S_IEXEC
                    )
        except zipfile.BadZipFile as exc:
            raise FFmpegNotFoundError(f"下载的文件损坏，无法解压：{exc}") from exc

    target_path.chmod(target_path.stat().st_mode | stat.S_IEXEC)

    if not verify_ffmpeg(target_path):
        raise FFmpegNotFoundError("下载的 ffmpeg 校验失败（`ffmpeg -version` 未成功执行）。")

    return str(target_path)
