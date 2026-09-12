"""脚本共用的控制台初始化。

为什么单独放一个模块：``scripts/`` 下的脚本**不都在包内**（有的直接
``python scripts/xxx.py`` 运行，项目根不在 ``sys.path`` 上），所以不能
假设 ``from qgb.utils import force_utf8_stdio`` 一定可用。

为什么需要它：本仓库所有面向人的输出都是中文，而 Python 在**非交互式**
环境下按区域设置挑编码 —— Linux/容器里常是 ``cp1252`` 或 ``ascii``，
于是第一句中文 ``print`` 就抛::

    UnicodeEncodeError: 'charmap' codec can't encode characters in position 0-8

CI 上 ``python -m qgb.dev.smoke`` 就是这么挂的；Windows 控制台自 3.6 起
走 UTF-8，所以本地永远复现不出来。
"""

from __future__ import annotations

import sys
from pathlib import Path


def use_utf8_console() -> bool:
    """把 stdout/stderr 切到 UTF-8。返回是否成功（失败也不该阻断脚本）。"""
    # 优先用项目内的实现（保持单一真相），不行再就地做一遍
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from qgb.utils import force_utf8_stdio  # noqa: PLC0415

        return force_utf8_stdio()
    except Exception:  # noqa: BLE001 - 脚本必须能在最小环境下继续跑
        pass

    ok = True
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            ok = False
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            ok = False
    return ok
