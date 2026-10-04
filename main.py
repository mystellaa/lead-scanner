# -*- coding: utf-8 -*-
"""Lead Scanner V0.1 —— 程序入口。

用法：
    python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证打包成 exe / 从任意目录启动时都能 import 同目录模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402


def _enable_dpi_awareness() -> None:
    """Windows 高分屏下界面不发虚。"""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    config.setup_logging()
    _enable_dpi_awareness()
    config.ensure_dirs()

    log = config.LOGGER
    log.info("启动 %s", config.APP_TITLE)
    log.info("数据目录: %s", config.DATA_DIR)

    try:
        from ui import run_app
    except ImportError as exc:
        msg = (f"界面模块加载失败：{exc}\n\n"
               "请确认当前 Python 带 Tkinter（官方安装包默认自带）。\n"
               "若使用绿色版/嵌入式 Python，需要自行补充 tcl/tk。")
        print(msg, file=sys.stderr)
        return 2

    run_app()
    log.info("程序退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
