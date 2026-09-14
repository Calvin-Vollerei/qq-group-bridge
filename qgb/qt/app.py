"""PySide6 界面的启动入口（毛玻璃 + 日夜主题）。

与旧 Tk 界面**并存**：``run_bridge.py --ui qt`` 走这里，默认仍是 Tk。
这样迁移期间随时可回退，出问题也不影响用户手上的可用版本。

职责：创建 QApplication、装配控制器、把 Shell 显示出来并进入事件循环。
窗口不在主线程创建是 Qt 的硬性要求，所以这里不做任何后台线程的事。
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """启动 PySide6 界面。返回进程退出码。"""
    argv = list(sys.argv if argv is None else argv)

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        print(
            f"未安装 PySide6（{exc}）。\n"
            "  请执行：pip install PySide6\n"
            "  或改用旧界面：python run_bridge.py",
            file=sys.stderr,
        )
        return 2

    # 高 DPI 屏上图标/文字清晰（Qt6 默认已开启缩放，这里只统一取整策略）
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(argv)
    # 应用级图标：任务栏、Alt+Tab 都取这里
    try:
        from .tray import app_icon

        app.setWindowIcon(app_icon())
    except Exception:  # noqa: BLE001
        pass
    app.setApplicationName("自动化群文件云转存软件（个人版）")
    app.setOrganizationName("qgb")

    # 统一字体：Windows 上用微软雅黑，缺失时回退默认
    font = QFont("Microsoft YaHei UI" if sys.platform == "win32" else "")
    font.setPointSize(10)
    app.setFont(font)

    from qgb.controller import AppController

    controller = AppController()
    controller.load()

    from .shell import Shell

    shell = Shell(controller)
    shell.show()
    shell.start()

    try:
        return app.exec()
    finally:
        try:
            controller.shutdown()
        except Exception:  # noqa: BLE001
            log.debug("控制器收尾失败", exc_info=True)
