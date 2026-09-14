"""定位 QLayout::addChildLayout 警告的触发点（逐步标记法）。

用法::

    set QT_FATAL_WARNINGS=1 && python scripts/qt-locate-warn.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402


def step(msg: str) -> None:
    """打印进度（stderr 无缓冲，崩溃时能看到最后一步）。"""
    print(f"STEP: {msg}", file=sys.stderr, flush=True)


def main() -> int:
    use_utf8_console()
    import os

    os.environ.setdefault("QGB_DATA_DIR", tempfile.mkdtemp(prefix="qgb-warn-"))

    from PySide6.QtWidgets import QApplication

    from qgb import paths

    paths.reset_cache()

    # 把 Qt 的 qWarning 转成 Python 异常，这样报错点就是**真正的出错行**
    # （比逐步加标记快得多；实测 addChildLayout 警告落在我自己的 _build_ui 里）
    from PySide6.QtCore import qInstallMessageHandler

    def _on_qt_message(mode, ctx, message) -> None:  # noqa: ANN001
        import traceback

        print(f"QTWARN: {message}", file=sys.stderr, flush=True)
        print("---- Python 调用栈 ----", file=sys.stderr, flush=True)
        traceback.print_stack(file=sys.stderr)
        raise RuntimeError(f"Qt 警告: {message}")

    qInstallMessageHandler(_on_qt_message)

    step("QApplication")
    app = QApplication(sys.argv)

    step("import controller")
    from qgb.controller import AppController

    step("AppController()")
    controller = AppController()

    step("controller.load()")
    controller.load()

    step("import Shell")
    from qgb.qt.shell import Shell

    step("Shell(controller)")
    shell = Shell(controller)

    step("shell.show()")
    shell.show()
    app.processEvents()

    step("iterate tabs")
    for i in range(shell.tabs.count()):
        step(f"  tab {i} = {shell.tabs.tabText(i)}")
        shell.tabs.setCurrentIndex(i)
        app.processEvents()

    step("done")
    try:
        controller.shutdown()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
