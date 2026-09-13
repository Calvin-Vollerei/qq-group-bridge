"""一眼判定毛玻璃是否生效：把窗口摆在**明暗对比强**的背景上。

原理：真毛玻璃会把**背后的内容**模糊并轻微提亮/调色。
所以做两块背景：
  · 左边一块纯白、右边一块纯黑（同一个窗口内部）
  · 顶层是一个小玻璃窗口

如果毛玻璃生效：小窗口跨在黑白交界处时，**窗口左右两半的亮度会明显不同**
（因为背后一半是白、一半是黑，被模糊后透上来）。
如果只是"自绘半透明"：也会有一点差别，但"模糊"看不出；
如果材质完全没生效：窗口左右亮度几乎一样（纯色）。

用法::

    python scripts/glass-verify.py             # 显示验证窗口
    python scripts/glass-verify.py --auto 20   # 20 秒后自动退出
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402  (保证中文输出不乱码)
from qgb.qt import glass  # noqa: E402


class BackgroundWindow:
    """左右黑白各半的底板，用来给玻璃窗口当"背后的内容"。"""

    def __init__(self):
        from PySide6.QtWidgets import QHBoxLayout, QWidget

        self.win = QWidget()
        self.win.setWindowTitle("背景板（左白 / 右黑）— 把玻璃窗口拖到交界处看")
        self.win.resize(900, 560)
        lay = QHBoxLayout(self.win)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        left = QWidget()
        left.setStyleSheet("background: #FFFFFF;")
        right = QWidget()
        right.setStyleSheet("background: #000000;")
        lay.addWidget(left)
        lay.addWidget(right)


class GlassWindow:
    """按指定路线做的小玻璃窗口，内容是一条横向文字（便于看模糊）。"""

    def __init__(self, route: str, dark: bool = True):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

        self.route = route
        self.cap = glass.detect()

        self.win = QWidget()
        self.win.setWindowTitle(f"玻璃窗口（路线 {route}）— 拖到黑白交界处")
        self.win.resize(520, 260)

        if route == "C":
            self.win.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            self.win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        lay = QVBoxLayout(self.win)
        lay.setContentsMargins(18, 18, 18, 18)
        lbl = QLabel(
            "判定方法：把这个窗口拖到背景板的黑/白交界处。\n\n"
            "· 左右两半亮度明显不同 → 背后内容真的透上来了 ✅\n"
            "· 左右几乎一样、整体是均匀色块 → 材质没生效（只是自绘半透明）\n\n"
            f"当前路线：{route}（{self.cap.describe()}）"
        )
        lbl.setWordWrap(True)
        if route == "C":
            lbl.setStyleSheet("color: #EAEDF4;")
            self.win.setStyleSheet(
                "QWidget { background: rgba(20, 22, 28, 0.45); border-radius: 14px; }"
            )
        else:
            # 原生材质：文字用白色，窗口本身**不画背景**（把合成交给 DWM）
            lbl.setStyleSheet("color: #EAEDF4;")
            self.win.setStyleSheet("QWidget { background: transparent; }")
        lay.addWidget(lbl)

    def apply(self) -> str:
        hwnd = int(self.win.winId())
        if self.route == "A":
            ok = glass.apply_route_a(hwnd, dark=True, kind=glass.BACKDROP_ACRYLIC)
            return f"路线 A（DWM 亚克力）：{'调用成功' if ok else '调用失败'}"
        if self.route == "B":
            ok = glass.apply_route_b(hwnd, dark=True)
            return f"路线 B（亚克力 API）：{'调用成功' if ok else '调用失败'}"
        glass.clear(hwnd)
        return "路线 C（自绘半透明）：不依赖系统合成"


def main() -> int:
    use_utf8_console()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    argv = sys.argv[1:]
    route = "A"
    if "--route" in argv:
        route = argv[argv.index("--route") + 1].upper()
    auto = 0
    if "--auto" in argv:
        i = argv.index("--auto")
        auto = int(argv[i + 1]) if i + 1 < len(argv) else 0

    app = QApplication(sys.argv)
    print("=" * 72)
    print("毛玻璃验证工具")
    print("=" * 72)
    print(f"  系统 build：{glass.windows_build()}")
    print(f"  探测结果：{glass.detect().describe()}")
    print(f"  本次路线：{route}")

    bg = BackgroundWindow()
    bg.win.move(120, 120)
    bg.win.show()

    gw = GlassWindow(route)
    gw.win.move(300, 320)          # 跨在黑白交界附近
    gw.win.show()
    app.processEvents()
    print(f"  {gw.apply()}")
    print()
    print("  把「玻璃窗口」拖到背景板的黑/白交界处，看左右两半亮度是否不同。")
    if auto:
        print(f"  （{auto} 秒后自动退出）")
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
