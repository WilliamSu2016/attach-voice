"""tests/test_settings.py — src/utils/settings.py 的纯单测。

用真实的 `QSettings(QSettings.Format.IniFormat, ...)` 指向 `tmp_path` 下的临时 ini 文件
（非 mock），既验证真实的读写路径，又不会污染开发者本机真实的注册表项。
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings

from src.utils.settings import AppSettings


def _backing(tmp_path: Path, name: str = "settings.ini") -> QSettings:
    return QSettings(str(tmp_path / name), QSettings.Format.IniFormat)


# ---------------------------------------------------------------------------
# 首次运行 / 缺失键 → 合理默认值，不崩溃
# ---------------------------------------------------------------------------


def test_defaults_when_ini_is_empty(tmp_path):
    settings = AppSettings(_backing(tmp_path))
    assert settings.voice() is None
    assert settings.rate() == 0
    assert settings.pitch() == 0
    assert settings.output_dir() == ""
    assert settings.mix_mode() == "replace"
    assert settings.original_volume() == 0.2


def test_defaults_when_keys_hold_corrupted_values(tmp_path):
    backing = _backing(tmp_path)
    backing.setValue("tts/rate", "not-a-number")
    backing.setValue("tts/pitch", "also-not-a-number")
    backing.setValue("mix/mode", "not-a-valid-mode")
    backing.setValue("mix/original_volume", "not-a-float")
    backing.sync()

    settings = AppSettings(backing)
    assert settings.rate() == 0
    assert settings.pitch() == 0
    assert settings.mix_mode() == "replace"
    assert settings.original_volume() == 0.2


def test_original_volume_out_of_range_falls_back_to_default(tmp_path):
    backing = _backing(tmp_path)
    backing.setValue("mix/original_volume", 5.0)
    backing.sync()
    settings = AppSettings(backing)
    assert settings.original_volume() == 0.2


# ---------------------------------------------------------------------------
# 各字段读写往返
# ---------------------------------------------------------------------------


def test_voice_roundtrip(tmp_path):
    settings = AppSettings(_backing(tmp_path))
    settings.set_voice("zh-CN-XiaoxiaoNeural")
    assert settings.voice() == "zh-CN-XiaoxiaoNeural"


def test_rate_and_pitch_roundtrip(tmp_path):
    settings = AppSettings(_backing(tmp_path))
    settings.set_rate(25)
    settings.set_pitch(-10)
    assert settings.rate() == 25
    assert settings.pitch() == -10


def test_output_dir_roundtrip(tmp_path):
    settings = AppSettings(_backing(tmp_path))
    settings.set_output_dir(str(tmp_path))
    assert settings.output_dir() == str(tmp_path)


def test_mix_mode_roundtrip_and_rejects_invalid(tmp_path):
    settings = AppSettings(_backing(tmp_path))
    settings.set_mix_mode("overlay")
    assert settings.mix_mode() == "overlay"
    settings.set_mix_mode("bogus-mode")
    assert settings.mix_mode() == "replace"


def test_original_volume_roundtrip(tmp_path):
    settings = AppSettings(_backing(tmp_path))
    settings.set_original_volume(0.75)
    assert settings.original_volume() == 0.75


# ---------------------------------------------------------------------------
# 跨实例持久化（模拟“关闭应用并重启”）
# ---------------------------------------------------------------------------


def test_settings_persist_across_new_appsettings_instance_same_ini(tmp_path):
    ini_path = tmp_path / "shared.ini"

    first = AppSettings(QSettings(str(ini_path), QSettings.Format.IniFormat))
    first.set_voice("zh-CN-YunxiNeural")
    first.set_rate(10)
    first.set_pitch(-5)
    first.set_output_dir(str(tmp_path))
    first.set_mix_mode("overlay")
    first.set_original_volume(0.6)
    first.sync()

    # 模拟"关闭应用"：不复用同一个 QSettings 对象，构造全新实例指向同一份 ini 文件，
    # 相当于"重启应用"后重新从磁盘读取。
    second = AppSettings(QSettings(str(ini_path), QSettings.Format.IniFormat))
    assert second.voice() == "zh-CN-YunxiNeural"
    assert second.rate() == 10
    assert second.pitch() == -5
    assert second.output_dir() == str(tmp_path)
    assert second.mix_mode() == "overlay"
    assert second.original_volume() == 0.6
