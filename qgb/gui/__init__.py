"""tkinter 界面层。

本包只负责「显示」与「转发用户操作」；所有业务逻辑在 :mod:`qgb.controller`，
因此核心逻辑可以脱离界面单独测试。
"""

from .app import MainWindow, main

__all__ = ["MainWindow", "main"]
