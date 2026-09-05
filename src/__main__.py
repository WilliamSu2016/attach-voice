"""应用入口：`python -m src`。

支持 `--version`（打印版本后退出，不启动 GUI）；其余情况启动 F07 实现的主窗口。
"""

import argparse
import sys

from src import __app_name__, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src", description=f"{__app_name__} — AI 旁白配音"
    )
    parser.add_argument(
        "--version", action="version", version=f"{__app_name__} {__version__}"
    )
    parser.parse_args(argv)

    from PyQt6.QtWidgets import QApplication

    from src.gui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    window = MainWindow()
    window.resize(900, 700)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
