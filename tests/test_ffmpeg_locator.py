"""tests/test_ffmpeg_locator.py — src/utils/ffmpeg_locator.py 的纯单测。

不依赖真实网络下载；下载相关测试通过 monkeypatch 隔离 urllib.request.urlretrieve。
"""
from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

import pytest

from src.utils import ffmpeg_locator as fl


# ---------------------------------------------------------------------------
# verify_ffmpeg
# ---------------------------------------------------------------------------


def test_verify_ffmpeg_true_on_returncode_zero(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(fl.subprocess, "run", fake_run)
    assert fl.verify_ffmpeg("fake_ffmpeg") is True


def test_verify_ffmpeg_false_on_nonzero_returncode(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode=1)

    monkeypatch.setattr(fl.subprocess, "run", fake_run)
    assert fl.verify_ffmpeg("fake_ffmpeg") is False


def test_verify_ffmpeg_false_on_missing_executable(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(fl.subprocess, "run", fake_run)
    assert fl.verify_ffmpeg("does_not_exist") is False


def test_verify_ffmpeg_false_on_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=10)

    monkeypatch.setattr(fl.subprocess, "run", fake_run)
    assert fl.verify_ffmpeg("slow_ffmpeg") is False


# ---------------------------------------------------------------------------
# find_ffmpeg
# ---------------------------------------------------------------------------


def test_find_ffmpeg_returns_none_when_nothing_available(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)
    monkeypatch.setattr(fl.shutil, "which", lambda name: None)
    assert fl.find_ffmpeg() is None


def test_find_ffmpeg_prefers_app_bin_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe_path = bin_dir / fl._FFMPEG_EXE_NAME
    exe_path.write_text("fake")

    # PATH 中也存在一个候选，但应用目录优先级更高，永远不该被用到
    monkeypatch.setattr(fl.shutil, "which", lambda name: "/usr/bin/ffmpeg_should_not_be_used")
    monkeypatch.setattr(fl, "verify_ffmpeg", lambda p: True)

    assert fl.find_ffmpeg() == str(exe_path)


def test_find_ffmpeg_falls_back_to_path(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)  # 应用目录 bin/ 不存在

    which_target = tmp_path / "path_ffmpeg"
    which_target.write_text("fake")
    monkeypatch.setattr(fl.shutil, "which", lambda name: str(which_target))
    monkeypatch.setattr(fl, "verify_ffmpeg", lambda p: True)

    assert fl.find_ffmpeg() == str(which_target)


def test_find_ffmpeg_falls_back_to_user_configured_path(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)
    monkeypatch.setattr(fl.shutil, "which", lambda name: None)

    user_path = tmp_path / "user_ffmpeg"
    user_path.write_text("fake")
    monkeypatch.setattr(fl, "verify_ffmpeg", lambda p: True)

    assert fl.find_ffmpeg(user_configured_path=str(user_path)) == str(user_path)


def test_find_ffmpeg_never_raises_on_unexpected_error(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)

    def boom(name):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr(fl.shutil, "which", boom)
    assert fl.find_ffmpeg() is None


def test_find_ffmpeg_skips_candidate_that_fails_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / fl._FFMPEG_EXE_NAME).write_text("fake but broken")

    which_target = tmp_path / "path_ffmpeg"
    which_target.write_text("fake, works")
    monkeypatch.setattr(fl.shutil, "which", lambda name: str(which_target))

    def fake_verify(path):
        return str(path) == str(which_target)

    monkeypatch.setattr(fl, "verify_ffmpeg", fake_verify)

    assert fl.find_ffmpeg() == str(which_target)


# ---------------------------------------------------------------------------
# FFmpegNotFoundError
# ---------------------------------------------------------------------------


def test_ffmpeg_not_found_error_default_message_mentions_download():
    err = fl.FFmpegNotFoundError()
    assert "下载" in str(err)


def test_ffmpeg_not_found_error_custom_message():
    err = fl.FFmpegNotFoundError("自定义消息")
    assert str(err) == "自定义消息"


# ---------------------------------------------------------------------------
# download_ffmpeg
# ---------------------------------------------------------------------------


def _make_fake_zip(archive_path: Path, exe_name: str) -> None:
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr(f"ffmpeg-release-essentials/bin/{exe_name}", b"fake ffmpeg binary")


def test_download_ffmpeg_rejects_non_windows(monkeypatch):
    monkeypatch.setattr(fl.platform, "system", lambda: "Linux")
    with pytest.raises(fl.FFmpegNotFoundError):
        fl.download_ffmpeg()


def test_download_ffmpeg_success(monkeypatch, tmp_path):
    monkeypatch.setattr(fl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)

    def fake_urlretrieve(url, filename, reporthook=None):
        _make_fake_zip(Path(filename), fl._FFMPEG_EXE_NAME)
        if reporthook:
            reporthook(1, 100, 100)
        return str(filename), None

    monkeypatch.setattr(fl.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(fl, "verify_ffmpeg", lambda p: True)

    progress_values = []
    result_path = fl.download_ffmpeg(progress_callback=progress_values.append)

    assert result_path == str(tmp_path / "bin" / fl._FFMPEG_EXE_NAME)
    assert Path(result_path).is_file()
    assert progress_values == [1.0]


def test_download_ffmpeg_network_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(fl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)

    def fake_urlretrieve(url, filename, reporthook=None):
        raise OSError("network down")

    monkeypatch.setattr(fl.urllib.request, "urlretrieve", fake_urlretrieve)

    with pytest.raises(fl.FFmpegNotFoundError):
        fl.download_ffmpeg()


def test_download_ffmpeg_missing_exe_in_archive_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(fl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)

    def fake_urlretrieve(url, filename, reporthook=None):
        with zipfile.ZipFile(filename, "w") as zf:
            zf.writestr("ffmpeg-release-essentials/README.txt", b"no ffmpeg here")
        return str(filename), None

    monkeypatch.setattr(fl.urllib.request, "urlretrieve", fake_urlretrieve)

    with pytest.raises(fl.FFmpegNotFoundError):
        fl.download_ffmpeg()


def test_download_ffmpeg_bad_zip_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(fl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)

    def fake_urlretrieve(url, filename, reporthook=None):
        Path(filename).write_bytes(b"not a real zip file")
        return str(filename), None

    monkeypatch.setattr(fl.urllib.request, "urlretrieve", fake_urlretrieve)

    with pytest.raises(fl.FFmpegNotFoundError):
        fl.download_ffmpeg()


def test_download_ffmpeg_verification_failure_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(fl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl, "_app_dir", lambda: tmp_path)

    def fake_urlretrieve(url, filename, reporthook=None):
        _make_fake_zip(Path(filename), fl._FFMPEG_EXE_NAME)
        return str(filename), None

    monkeypatch.setattr(fl.urllib.request, "urlretrieve", fake_urlretrieve)
    monkeypatch.setattr(fl, "verify_ffmpeg", lambda p: False)

    with pytest.raises(fl.FFmpegNotFoundError):
        fl.download_ffmpeg()
