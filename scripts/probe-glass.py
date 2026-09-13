"""毛玻璃技术验证：三条路各做一次，看真机效果。

Windows 上要"真"毛玻璃（窗口背后内容被模糊）有三种可行路线，能力差别很大：

A. ``DwmSetWindowAttribute`` + ``DWMWA_SYSTEMBACKDROP_TYPE``（Win11 原生 Mica/Acrylic）
   —— 效果最"系统级"，但只在 Win11 22H2+ 有效，且要求窗口背景透明。

B. ``SetWindowCompositionAttribute`` + ``ACCENT_ENABLE_ACRYLICBLURBEHIND``
   （Win10 1803+ 的亚克力）—— 兼容性更广，但 Win11 上可能有拖影。

C. 纯 Qt 模拟：窗口无边框 + 半透明 + ``QGraphicsBlurEffect`` 模糊**自身背景**
   —— 跨平台稳定，但模糊的是窗口自己的内容，不是背后的桌面（"伪毛玻璃"）。

本脚本创建三个窗口分别试 A / B / C，并把结果打印出来；
用 ``--hold 0`` 可在无头环境只做能力探测（不显示窗口）。

用法::

    python scripts/probe-glass.py            # 显示三个窗口（人工看效果）
    python scripts/probe-glass.py --auto 6    # 显示 6 秒后自动退出
    python scripts/probe-glass.py --probe-only  # 只打印能力探测结果
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


# ---------------------------------------------------------------- 能力探测

def detect_windows_build() -> int:
    try:
        return int(ctypes.windll.sysinfo.GetVersionExW.__doc__ or 0) if False else (
            ctypes.windll.ntdll.RtlGetVersion
            and _build()
        )
    except Exception:
        return 0


def _build() -> int:
    class OSVERSIONINFOEXW(ctypes.Structure):
        _fields_ = [
            ("dwOSVersionInfoSize", wintypes.DWORD),
            ("dwMajorVersion", wintypes.DWORD),
            ("dwMinorVersion", wintypes.DWORD),
            ("dwBuildNumber", wintypes.DWORD),
            ("dwPlatformId", wintypes.DWORD),
            ("szCSDVersion", wintypes.WCHAR * 128),
        ]

    info = OSVERSIONINFOEXW()
    info.dwOSVersionInfoSize = ctypes.sizeof(OSVERSIONINFOEXW)
    ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info))
    return int(info.dwBuildNumber)


DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33


def apply_mica(hwnd: int, dark: bool = True) -> tuple[bool, str]:
    """路线 A：DWM 系统背景材质（Mica / Acrylic）。"""
    try:
        dwm = ctypes.windll.dwmapi
        # 2 = Mica, 3 = Acrylic, 4 = Tabbed
        backdrop = ctypes.c_int(3)
        r1 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop),
        )
        dark_flag = ctypes.c_int(1 if dark else 0)
        r2 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark_flag), ctypes.sizeof(dark_flag),
        )
        # 圆角：2 = 圆角
        corner = ctypes.c_int(2)
        r3 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner), ctypes.sizeof(corner),
        )
        return (r1 == 0 and r2 == 0), f"backdrop={r1} dark={r2} corner={r3}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_ENABLE_BLURBEHIND = 3
WCA_ACCENT_POLICY = 19


def apply_acrylic(hwnd: int, tint: int = 0xCC1A1A1A) -> tuple[bool, str]:
    """路线 B：SetWindowCompositionAttribute 亚克力。"""
    try:
        accent = ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = tint          # AABBGGRR
        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        ok = ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data)
        )
        return bool(ok), f"SetWindowCompositionAttribute={ok}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------- 主题与窗口

DARK = {
    "bg": "rgba(24, 26, 32, 0.62)",
    "card": "rgba(44, 48, 58, 0.55)",
    "border": "rgba(255, 255, 255, 0.10)",
    "text": "#E8EAF0",
    "muted": "#9AA3B2",
    "accent": "#4C8DFF",
}
LIGHT = {
    "bg": "rgba(246, 248, 252, 0.66)",
    "card": "rgba(255, 255, 255, 0.55)",
    "border": "rgba(0, 0, 0, 0.08)",
    "text": "#1B1F27",
    "muted": "#5C6570",
    "accent": "#2F6FEB",
}


def glass_qss(p: dict) -> str:
    return f"""
    QWidget#Root {{ background: transparent; }}
    QFrame#Card {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: 14px;
    }}
    QLabel {{ color: {p['text']}; background: transparent; }}
    QLabel#Muted {{ color: {p['muted']}; }}
    QLabel#Title {{ font-size: 17px; font-weight: 600; }}
    QPushButton {{
        color: {p['text']};
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: 10px;
        padding: 7px 14px;
    }}
    QPushButton:hover {{ background: {p['accent']}; color: #FFFFFF; }}
    QTextEdit {{
        background: {p['card']};
        color: {p['text']};
        border: 1px solid {p['border']};
        border-radius: 10px;
    }}
    """


def build_window(route: str, theme: dict, title: str):
    """按路线造一个窗口，返回 (window, 状态文本)。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
    )

    win = QWidget()
    win.setWindowTitle(title)
    win.resize(560, 360)

    if route in ("A", "B"):
        # 原生材质要求窗口背景透明（否则看不到材质）
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        win.setStyleSheet("QWidget#Root { background: transparent; }")
    else:
        # 路线 C：无边框 + 半透明，靠自己画底
        win.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        win.setStyleSheet(
            f"QWidget#Root {{ background: {theme['bg']}; border-radius: 16px; }}"
        )

    root = QWidget(win)
    root.setObjectName("Root")
    outer = QVBoxLayout(win)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(root)

    lay = QVBoxLayout(root)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)

    title_lbl = QLabel(f"路线 {route}：{title}")
    title_lbl.setObjectName("Title")
    lay.addWidget(title_lbl)

    card = QFrame()
    card.setObjectName("Card")
    cl = QVBoxLayout(card)
    cl.setContentsMargins(14, 12, 14, 12)
    hint = QLabel("毛玻璃卡片：半透明 + 描边 + 圆角")
    hint.setObjectName("Muted")
    cl.addWidget(hint)

    row = QHBoxLayout()
    for text in ("主按钮", "次按钮", "设置"):
        row.addWidget(QPushButton(text))
    row.addStretch(1)
    cl.addLayout(row)
    lay.addWidget(card)

    log = QTextEdit()
    log.setReadOnly(True)
    log.setPlainText(
        "背景若能看到模糊的桌面/其它窗口 → 真毛玻璃生效\n"
        "若只是纯色半透明 → 该路线在当前系统上不可用，需要退回 Qt 模拟"
    )
    lay.addWidget(log, 1)

    root.setStyleSheet(glass_qss(theme) + win.styleSheet())
    return win


def main() -> int:
    use_utf8_console()
    build = _build()
    print("=" * 70)
    print("毛玻璃能力探测")
    print("=" * 70)
    print(f"  Windows build : {build}")
    print(f"  Win11 22H2+   : {'是（支持 DWM 系统背景材质）' if build >= 22621 else '否（Mica/Acrylic DWM 属性可能无效）'}")
    print(f"  DWM 可用      : {bool(ctypes.windll.dwmapi)}")
    print(f"  Composition   : {bool(ctypes.windll.user32.SetWindowCompositionAttribute)}")

    if "--probe-only" in sys.argv:
        return 0

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    print(f"  Qt 平台插件   : {app.platformName()}")

    auto = 0
    if "--auto" in sys.argv:
        i = sys.argv.index("--auto")
        auto = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 6

    # 路线 A / B 各一个窗口，最后一个是纯 Qt 模拟
    results: list[str] = []
    windows = []
    specs = (
        ("A", DARK, "DWM 系统背景材质（Acrylic）+ 深色"),
        ("B", DARK, "SetWindowCompositionAttribute 亚克力 + 深色"),
        ("C", LIGHT, "纯 Qt 模拟（无边框半透明）+ 浅色"),
    )
    for route, theme, title in specs:
        win = build_window(route, theme, title)
        win.move(120 + 40 * len(windows), 120 + 40 * len(windows))
        win.show()
        windows.append(win)
        app.processEvents()
        hwnd = int(win.winId())
        if route == "A":
            ok, detail = apply_mica(hwnd, dark=True)
            results.append(f"路线 A（DWM 材质）  : {'✅ 调用成功' if ok else '❌ 失败'}  {detail}")
        elif route == "B":
            ok, detail = apply_acrylic(hwnd, tint=0xCC1A1A1A)
            results.append(f"路线 B（亚克力 API）: {'✅ 调用成功' if ok else '❌ 失败'}  {detail}")
        else:
            results.append("路线 C（Qt 模拟）    : ✅ 始终可用（不依赖系统）")

    print()
    for r in results:
        print(f"  {r}")
    print()
    print("  请肉眼确认：窗口背后若能看到**模糊的桌面**，说明该路线真毛玻璃生效。")
    if auto:
        print(f"  （{auto} 秒后自动退出）")
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
