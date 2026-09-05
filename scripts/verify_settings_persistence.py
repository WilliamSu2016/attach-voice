"""scripts/verify_settings_persistence.py — F09 配置持久化的自动化验收脚本。

背景：F09 的 2 条 manual 验证项要求"设置后关闭应用并重启，验证设置被正确恢复"与
"清空注册表/配置项后启动，验证使用默认值且无异常"，agent 无法真正关闭进程重启或清空
开发者本机真实注册表。沿用 F06（真实进程/文件观察）/F07（QTest 真实点击 + 客观指标）/
F08（QTimer 心跳客观证据）的先例，采用以下等价方案：

1. 用真实 `QSettings(QSettings.Format.IniFormat, ...)` 指向临时 ini 文件（走真实读写路径，
   非 mock，只是不落到开发者本机真实注册表）：构造第一个 `MainWindow` 实例，真实操作各控件
   改变设置并销毁该实例；再构造第二个全新的 `MainWindow` 实例指向同一份 ini 文件，断言其
   启动时各控件回填的值与保存前一致。两个独立实例读写同一份持久化存储，在数据层面与
   "关闭应用并重启"等价。
2. 指向一个全新的空 ini 文件（模拟"从未设置过/配置已清空"），构造 `MainWindow`，断言
   构造过程不抛异常，且各控件显示预期的合理默认值。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from src.gui.main_window import MainWindow, _MIX_MODES  # noqa: E402
from src.utils.settings import AppSettings  # noqa: E402

checks: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, condition, detail))
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    app = QApplication.instance() or QApplication([])

    # ------------------------------------------------------------------
    # 1) 命令行验证项（AST/文本扫描）在此脚本内重复确认一次，作为完整验收记录的一部分
    # ------------------------------------------------------------------
    import glob

    hits = [
        f for f in glob.glob("src/**/*.py", recursive=True)
        if "QSettings" in open(f, encoding="utf-8").read()
    ]
    check(
        "QSettings 仅出现在 src/utils/settings.py 中",
        hits in (["src/utils/settings.py"], ["src\\utils\\settings.py"]),
        str(hits),
    )

    # ------------------------------------------------------------------
    # 2) "设置后关闭应用并重启" 的等价验证：两个独立 MainWindow 实例 + 同一份临时 ini 文件
    # ------------------------------------------------------------------
    work_dir = REPO_ROOT / "assets" / "samples"
    work_dir.mkdir(parents=True, exist_ok=True)
    ini_path = work_dir / "verify_settings_restart.ini"
    if ini_path.exists():
        ini_path.unlink()

    def make_settings() -> AppSettings:
        return AppSettings(QSettings(str(ini_path), QSettings.Format.IniFormat))

    window1 = MainWindow(settings=make_settings())
    # 真实操作各控件（走真实信号/槽，非直接写 AppSettings）
    window1._voice_selector._voice_combo.setCurrentIndex(
        min(3, window1._voice_selector._voice_combo.count() - 1)
    )
    chosen_voice = window1._voice_selector.selected_voice_short_name()
    window1._voice_selector.set_rate(15)
    window1._voice_selector.set_pitch(-8)
    overlay_index = next(i for i, (_, mode) in enumerate(_MIX_MODES) if mode == "overlay")
    window1._mix_mode_combo.setCurrentIndex(overlay_index)
    window1._volume_slider.setValue(66)
    window1._settings.set_output_dir(str(work_dir))
    window1._settings.sync()
    window1.deleteLater()
    app.processEvents()

    # 构造全新实例，指向同一份 ini 文件——模拟"关闭应用并重启"
    window2 = MainWindow(settings=make_settings())
    restored_voice = window2._voice_selector.selected_voice_short_name()
    restored_rate = window2._voice_selector._rate_spin.value()
    restored_pitch = window2._voice_selector._pitch_spin.value()
    restored_mix_index = window2._mix_mode_combo.currentIndex()
    restored_volume = window2._volume_slider.value()
    restored_output_dir = window2._settings.output_dir()

    check(
        "重启后音色被正确恢复",
        restored_voice == chosen_voice,
        f"saved={chosen_voice}, restored={restored_voice}",
    )
    check("重启后语速被正确恢复", restored_rate == 15, f"restored={restored_rate}")
    check("重启后音调被正确恢复", restored_pitch == -8, f"restored={restored_pitch}")
    check(
        "重启后混音模式被正确恢复",
        restored_mix_index == overlay_index,
        f"expected_index={overlay_index}, restored_index={restored_mix_index}",
    )
    check("重启后原声音量被正确恢复", restored_volume == 66, f"restored={restored_volume}")
    check(
        "重启后输出目录被正确恢复",
        restored_output_dir == str(work_dir),
        f"restored={restored_output_dir}",
    )
    window2.deleteLater()
    app.processEvents()

    # ------------------------------------------------------------------
    # 3) "清空配置后启动" 的等价验证：全新空 ini 文件 + 构造 MainWindow 不抛异常，默认值正确
    # ------------------------------------------------------------------
    empty_ini_path = work_dir / "verify_settings_empty.ini"
    if empty_ini_path.exists():
        empty_ini_path.unlink()

    construction_error: Exception | None = None
    window3 = None
    try:
        empty_settings = AppSettings(QSettings(str(empty_ini_path), QSettings.Format.IniFormat))
        window3 = MainWindow(settings=empty_settings)
    except Exception as exc:  # noqa: BLE001 — 验证目标就是"绝不抛异常"
        construction_error = exc

    check(
        "配置为空时构造 MainWindow 不抛异常",
        construction_error is None,
        f"error={construction_error!r}" if construction_error else "无异常",
    )

    if window3 is not None:
        default_replace_index = next(i for i, (_, mode) in enumerate(_MIX_MODES) if mode == "replace")
        check(
            "配置为空时音色下拉保留有效选择（不因缺失键崩溃，任选一个可用音色）",
            window3._voice_selector.selected_voice_short_name() is not None,
            str(window3._voice_selector.selected_voice_short_name()),
        )
        check(
            "配置为空时语速为默认值 0",
            window3._voice_selector._rate_spin.value() == 0,
            str(window3._voice_selector._rate_spin.value()),
        )
        check(
            "配置为空时音调为默认值 0",
            window3._voice_selector._pitch_spin.value() == 0,
            str(window3._voice_selector._pitch_spin.value()),
        )
        check(
            "配置为空时混音模式为默认值 replace",
            window3._mix_mode_combo.currentIndex() == default_replace_index,
            str(window3._mix_mode_combo.currentIndex()),
        )
        check(
            "配置为空时原声音量为默认值 0.20（滑块=20）",
            window3._volume_slider.value() == 20,
            str(window3._volume_slider.value()),
        )
        check(
            "配置为空时输出目录为默认空字符串",
            window3._settings.output_dir() == "",
            repr(window3._settings.output_dir()),
        )
        window3.deleteLater()
        app.processEvents()

    print()
    passed = sum(1 for _, ok, _ in checks if ok)
    print(f"共 {len(checks)} 项检查，通过 {passed} 项。")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
