"""验证 blur.py 的两种序列（照抄 pywinstyles 实测生效的调用）。

分别开两个窗口：`acrylic`（AccentState=4）与 `blur`（AccentState=3），
再开一个 `none` 作对照。请指出哪几个真的模糊。

用法::

    python scripts/probe-blur.py            # 显示三个窗口
    python scripts/probe-blur.py --auto 30  # 30 秒后自动退出
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402

from qgb.qt import blur  # noqa: E402


def build(label: str):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    win = QWidget()
    win.resize(400, 200)
    win.setWindowTitle(label)
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    win.setStyleSheet("QWidget { background: transparent; }")

    lay = QVBoxLayout(win)
    lay.setContentsMargins(14, 14, 14, 14)
    text = QLabel(f"{label}\n\n看背后内容：模糊 = 生效；清晰 = 没生效")
    text.setStyleSheet("color: #FFFFFF; font-size: 14px;")
    text.setWordWrap(True)
    lay.addWidget(text)
    return win


def main() -> int:
    use_utf8_console()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    argv = sys.argv[1:]
    auto = 0
    if "--auto" in argv:
        i = argv.index("--auto")
        auto = int(argv[i + 1]) if i + 1 < len(argv) else 0

    app = QApplication(sys.argv)
    print("=" * 70)
    print("blur.py 两种序列验证（抄自 pywinstyles 实测生效的调用）")
    print("=" * 70)

    plans = (
        ("acrylic", "acrylic — AccentState=4, color=0"),
        ("blur", "blur — AccentState=3, color=0x000000"),
        ("none", "none — 对照（无模糊）"),
    )
    wins = []
    for i, (style, label) in enumerate(plans):
        win = build(label)
        win.move(70 + i * 90, 90 + i * 70)
        win.show()
        app.processEvents()
        ok = blur.apply_style(int(win.winId()), style)
        print(f"  {label:42} 应用{'成功' if ok else '失败'}")
        wins.append(win)

    print()
    print("  请指出哪几个真的模糊（背景被糊掉、看不出细节）。")
    if auto:
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
