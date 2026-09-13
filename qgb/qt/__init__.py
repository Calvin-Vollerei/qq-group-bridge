"""PySide6 界面包（毛玻璃 + 日夜主题）。

模块划分：

    themes.py    配色与全局 QSS（单一数据源）
    glass.py     毛玻璃三路线 + 自动降级探测
    widgets.py   可复用控件（卡片、指标、日志视图、提示条）
    shell.py     主窗口（标题栏、标签页、主题切换、玻璃路由）

与旧 Tk 界面的关系：**并存**。``qgb/gui`` 仍是默认实现，本包通过
``run_bridge.py --ui qt`` 启用，便于逐页迁移与随时回退。
"""

from __future__ import annotations

__all__ = ["themes", "glass", "widgets", "shell"]
