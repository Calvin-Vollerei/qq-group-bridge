"""毛玻璃 + 日夜主题 原型（PySide6）。

用途：在把整个界面迁到 PySide6 之前，先用一个**可运行的成品**确认视觉效果
能否接受。包含：

* 三条毛玻璃路线（可用按钮实时切换，肉眼对比）
    A. DWM 系统背景材质（Acrylic / Mica）—— Win11 22H2+，最"系统级"
    B. SetWindowCompositionAttribute 亚克力 —— 兼容性更广
    C. 纯 Qt 模拟（无边框 + 半透明 + 自绘渐变/噪点）—— 跨平台稳定
* 日夜主题一键切换（同一窗口内切换，便于对比）
* 半透明卡片、圆角、描边等"毛玻璃 UI"常用元素

用法::

    python scripts/glass-demo.py              # 交互运行
    python scripts/glass-demo.py --route C    # 指定初始路线
    python scripts/glass-demo.py --theme light --auto 10
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402

# ============================================================ 原生毛玻璃

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def apply_native_backdrop(hwnd: int, *, dark: bool, kind: int = 3) -> str:
    """应用 DWM 系统背景材质。kind: 2=Mica 3=Acrylic 4=Tabbed。返回结果说明。"""
    try:
        dwm = ctypes.windll.dwmapi
        backdrop = ctypes.c_int(kind)
        r1 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop))
        flag = ctypes.c_int(1 if dark else 0)
        r2 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(flag), ctypes.sizeof(flag))
        corner = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner), ctypes.sizeof(corner))
        return "OK" if (r1 == 0 and r2 == 0) else f"部分失败 backdrop={r1} dark={r2}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def apply_acrylic(hwnd: int, *, dark: bool) -> str:
    """亚克力（Win10 1803+）。"""
    try:
        tint = 0xCC1A1A1E if dark else 0xCCF2F4F8      # AABBGGRR
        accent = _ACCENT_POLICY()
        accent.AccentState = 4                          # ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = tint
        data = _WINCOMPATTRDATA()
        data.Attribute = 19                             # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        ok = ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data))
        return "OK" if ok else "调用返回 0"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def clear_native(hwnd: int) -> None:
    """清掉原生材质，便于切到路线 C。"""
    try:
        accent = _ACCENT_POLICY()
        accent.AccentState = 0                          # DISABLED
        data = _WINCOMPATTRDATA()
        data.Attribute = 19
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data))
        backdrop = ctypes.c_int(1)                      # 1 = None
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    except Exception:  # noqa: BLE001
        pass


# ============================================================ 主题

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "name": "夜间",
        "window_tint": "rgba(20, 22, 28, 0.35)",
        "card": "rgba(58, 62, 74, 0.55)",
        "card_hover": "rgba(72, 78, 92, 0.62)",
        "border": "rgba(255, 255, 255, 0.14)",
        "text": "#EAEDF4",
        "muted": "#A3ACBC",
        "accent": "#5B9BFF",
        "accent_text": "#FFFFFF",
        "input": "rgba(30, 33, 41, 0.55)",
    },
    "light": {
        "name": "日间",
        "window_tint": "rgba(248, 250, 253, 0.35)",
        "card": "rgba(255, 255, 255, 0.55)",
        "card_hover": "rgba(255, 255, 255, 0.72)",
        "border": "rgba(0, 0, 0, 0.10)",
        "text": "#1A1E26",
        "muted": "#5A6472",
        "accent": "#2F6FEB",
        "accent_text": "#FFFFFF",
        "input": "rgba(255, 255, 255, 0.65)",
    },
}


def qss(p: dict[str, str]) -> str:
    return f"""
    QWidget#Root {{ background: transparent; }}
    QFrame#Card {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: 14px;
    }}
    QLabel {{ color: {p['text']}; background: transparent; }}
    QLabel#Title {{ font-size: 16px; font-weight: 600; }}
    QLabel#Muted {{ color: {p['muted']}; }}
    QLabel#Hint {{ color: {p['muted']}; font-size: 12px; }}
    QPushButton {{
        color: {p['text']};
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: 9px;
        padding: 7px 13px;
    }}
    QPushButton:hover {{ background: {p['card_hover']}; }}
    QPushButton#Primary {{
        background: {p['accent']}; color: {p['accent_text']};
        border: 1px solid {p['accent']};
    }}
    QPushButton:checked {{
        background: {p['accent']}; color: {p['accent_text']};
        border: 1px solid {p['accent']};
    }}
    QTextEdit, QPlainTextEdit, QListWidget, QLineEdit {{
        background: {p['input']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 10px;
        padding: 6px;
    }}
    QTabWidget::pane {{ border: 1px solid {p['border']}; border-radius: 12px;
                        background: {p['card']}; }}
    QTabBar::tab {{
        color: {p['muted']}; background: transparent;
        padding: 7px 14px; margin-right: 4px; border-radius: 8px;
    }}
    QTabBar::tab:selected {{ color: {p['accent_text']}; background: {p['accent']}; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; }}
    QScrollBar::handle:vertical {{
        background: {p['border']}; border-radius: 5px; min-height: 24px;
    }}
    """


# ============================================================ 界面

class GlassWindow:
    def __init__(self, route: str, theme: str) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QPainter
        from PySide6.QtWidgets import (
            QApplication, QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
            QPushButton, QTabWidget, QVBoxLayout, QWidget,
        )

        self.route = route
        self.theme_key = theme
        self._Qt = Qt

        self.win = QWidget()
        self.win.setWindowTitle("毛玻璃与日夜主题原型 — QQ群文件搬运工")
        self.win.resize(920, 620)
        self.win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # 路线 C 需要无边框（自己画圆角底）；A/B 用系统边框以吃到系统材质
        self._apply_window_flags()

        root = QWidget(self.win)
        root.setObjectName("Root")
        outer = QVBoxLayout(self.win)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        lay = QVBoxLayout(root)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        # ---- 顶部：标题 + 主题切换 + 路线切换
        head = QHBoxLayout()
        title = QLabel("毛玻璃 / 日夜主题 原型")
        title.setObjectName("Title")
        head.addWidget(title)
        head.addStretch(1)

        self.btn_theme = QPushButton("切换主题：夜间")
        self.btn_theme.clicked.connect(self._toggle_theme)
        head.addWidget(self.btn_theme)
        lay.addLayout(head)

        # ---- 路线选择（可对比三种实现）
        row = QHBoxLayout()
        row.addWidget(QLabel("毛玻璃路线："))
        self.route_buttons: dict[str, QPushButton] = {}
        for code, label in (("A", "A 系统材质(Mica/Acrylic)"),
                            ("B", "B 亚克力 API"),
                            ("C", "C 纯 Qt 模拟")):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setChecked(code == route)
            b.clicked.connect(lambda _c=False, k=code: self.set_route(k))
            self.route_buttons[code] = b
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        # ---- 卡片区（演示毛玻璃卡片 + 控件）
        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(10)

        cl.addWidget(QLabel("卡片：半透明 + 描边 + 圆角（毛玻璃 UI 的基本块）"))
        hint = QLabel("把鼠标移到按钮上、切换主题与路线，观察质感是否自然")
        hint.setObjectName("Hint")
        cl.addWidget(hint)

        btns = QHBoxLayout()
        for text, primary in (("开始监控", True), ("暂停", False),
                              ("立即刷新", False), ("设置", False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("Primary")
            btns.addWidget(b)
        btns.addStretch(1)
        cl.addLayout(btns)
        lay.addWidget(card)

        # ---- 标签页（演示复杂内容也能套用同一套主题）
        tabs = QTabWidget()
        for name in ("监控", "群与规则", "QQ 登录", "网盘与凭据", "高级"):
            page = QWidget()
            pl = QVBoxLayout(page)
            editor = QPlainTextEdit()
            editor.setPlainText(
                f"[{name}] 这是一个示例页面。\n\n"
                "主题切换时，卡片、按钮、输入框、标签页、滚动条都应同步变色。\n"
                "毛玻璃路线切换时，窗口背景的模糊质感会变化：\n"
                "  · A/B 依赖 Windows 原生合成，背后桌面会被系统模糊\n"
                "  · C 由本程序自绘半透明底，不依赖系统，效果稳定但不会模糊桌面"
            )
            editor.setReadOnly(True)
            pl.addWidget(editor)
            tabs.addTab(page, name)
        lay.addWidget(tabs, 1)

        # ---- 状态行
        self.status = QLabel("")
        self.status.setObjectName("Hint")
        lay.addWidget(self.status)

        self._apply_theme()

    # ------------------------------------------------ 窗口表现

    def _apply_window_flags(self) -> None:
        Qt = self._Qt
        if self.route == "C":
            self.win.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        else:
            self.win.setWindowFlags(Qt.WindowType.Window)

    def set_route(self, route: str) -> None:
        self.route = route
        for code, btn in self.route_buttons.items():
            btn.setChecked(code == route)
        # 切换路线要重建窗口标志，Tk 那样"原地改"在 Qt 里不可靠
        self._apply_window_flags()
        self.win.show()                      # 重新显示让标志生效
        self._apply_native()

    def _apply_native(self) -> None:
        hwnd = int(self.win.winId())
        dark = self.theme_key == "dark"
        if self.route == "A":
            detail = apply_native_backdrop(hwnd, dark=dark, kind=3)   # Acrylic
        elif self.route == "B":
            detail = apply_acrylic(hwnd, dark=dark)
        else:
            clear_native(hwnd)
            detail = "自绘半透明底（不依赖系统合成）"
        self.status.setText(
            f"路线 {self.route}：{detail}　|　主题：{THEMES[self.theme_key]['name']}"
        )

    # ------------------------------------------------ 主题

    def _toggle_theme(self) -> None:
        self.theme_key = "light" if self.theme_key == "dark" else "dark"
        self._apply_theme()

    def _apply_theme(self) -> None:
        from PySide6.QtGui import QColor, QPalette

        p = THEMES[self.theme_key]
        self.btn_theme.setText(f"切换主题：{p['name']}")
        base = qss(p)
        if self.route == "C":
            base = f"QWidget#Root {{ background: {p['window_tint']};" \
                   f" border-radius: 16px; border: 1px solid {p['border']}; }}\n" + base
        self.win.setStyleSheet(base)

        # 让系统原生材质跟随明暗
        try:
            self._apply_native()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    use_utf8_console()
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    argv = sys.argv[1:]
    route = "A"
    theme = "dark"
    auto = 0
    if "--route" in argv:
        route = argv[argv.index("--route") + 1].upper()
    if "--theme" in argv:
        theme = argv[argv.index("--theme") + 1].lower()
    if "--auto" in argv:
        i = argv.index("--auto")
        auto = int(argv[i + 1]) if i + 1 < len(argv) else 0

    app = QApplication(sys.argv)
    print("=" * 70)
    print("毛玻璃 + 日夜主题 原型")
    print("=" * 70)
    print(f"  Qt 平台插件：{app.platformName()}")
    print(f"  初始路线：{route}    初始主题：{theme}")
    print("  按钮可实时切换：路线 A/B/C 与 夜间/日间")
    print("  观察要点：窗口背后若能看到模糊的桌面，即原生毛玻璃生效")
    print("=" * 70)

    gw = GlassWindow(route, theme)
    gw.win.show()
    gw._apply_native()

    if auto:
        print(f"  （{auto} 秒后自动退出）")
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
