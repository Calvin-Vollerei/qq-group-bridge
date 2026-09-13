"""用 pywinstyles 验证原生毛玻璃（第三方库，专门解决 Qt 拿不到 DWM 材质的问题）。

背景：我手搓 ctypes 试了四种组合（SYSTEMBACKDROP_TYPE / ExtendFrameIntoClientArea /
SetWindowCompositionAttribute 等），客户区一律是**纯色块**，没有纹理 ——
说明 Qt 的窗口结构拿不到 DWM 的材质合成。

``pywinstyles`` 是对这组 Win32 调用的成熟封装（维护活跃）。本探针把它支持的
几种样式各开一个窗口，并**直接采样客户区像素**判断"是否真的有内容透上来"
（纯色 = 没生效；有多种亮度 = 背后内容被合成进来了）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402

STYLES = (
    ("acrylic", "亚克力（有边框）"),
    ("!acrylic", "亚克力（无边框）"),
    ("mica", "Mica（有边框）"),
    ("aero", "Aero（有边框）"),
    ("transparent", "完全透明（对照，不模糊）"),
)


def build(style: str, title: str):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    win = QWidget()
    win.resize(400, 220)
    win.setWindowTitle(f"{style} — {title}")
    # 关键：窗口必须透明，否则 Qt 画的黑底会盖住材质
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    win.setStyleSheet("QWidget { background: transparent; }")
    # 变体：带 "!" 前缀的样式用**无边框**窗口 —— 验证合成路径是否不同
    if style.startswith("!"):
        win.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)

    lay = QVBoxLayout(win)
    lay.setContentsMargins(14, 14, 14, 14)
    lbl = QLabel(f"style = {style}\n{title}\n\n看客户区是否有纹理（不是纯色块）")
    lbl.setStyleSheet("color: #FFFFFF; font-size: 13px;")
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    return win


def main() -> int:
    use_utf8_console()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    try:
        import pywinstyles
    except ImportError:
        print("未安装 pywinstyles，请先：pip install pywinstyles", file=sys.stderr)
        return 2

    argv = sys.argv[1:]
    auto = 0
    if "--auto" in argv:
        i = argv.index("--auto")
        auto = int(argv[i + 1]) if i + 1 < len(argv) else 0

    app = QApplication(sys.argv)
    print("=" * 74)
    print("pywinstyles 原生毛玻璃验证")
    print("=" * 74)
    print(f"  pywinstyles {getattr(pywinstyles, '__version__', '?')}")
    print()

    wins = []
    for i, (style, desc) in enumerate(STYLES):
        win = build(style, desc)
        win.move(70 + i * 70, 70 + i * 48)
        win.show()
        app.processEvents()
        try:
            pywinstyles.apply_style(win, style.lstrip("!"))
            result = "已应用"
        except Exception as exc:  # noqa: BLE001
            result = f"失败：{type(exc).__name__}: {exc}"
        print(f"  {style:12} {desc:34} {result}")
        wins.append(win)

    print()
    print("  判定：客户区有纹理/能看到背后内容 = 生效；纯色块 = 没生效。")
    if auto:
        print(f"  （{auto} 秒后自动退出）")
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
