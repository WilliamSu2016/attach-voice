"""应用入口：`python -m src`。

MVP 脚手架阶段：仅支持 `--version`，GUI 启动在 F07 接入。
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
    print(f"{__app_name__} {__version__} — GUI 尚未接入（见 feature F07）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
