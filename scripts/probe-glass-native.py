"""原生毛玻璃四种组合对照测试 —— 一次性找出真正生效的那种。

背景（踩坑记录）：之前只设 ``DWMWA_SYSTEMBACKDROP_TYPE``，结果**只有系统画的标题栏
那条模糊，客户区死黑**。原因很可能是**没有把 DWM 帧延伸进客户区** ——
材质只覆盖在 DWM 帧范围内。

本探针把常见组合都试一遍，每个窗口用大字标注方案，人工对比：

    1. ACCENT_ACRYLIC   —— SetWindowCompositionAttribute + ACRYLICBLURBEHIND
                           窗口**不透明**（关键：alpha 为 0 时亚克力才明显）
    2. DWM+EXTEND       —— SYSTEMBACKDROP_TYPE(3) + DwmExtendFrameIntoClientArea(-1)
    3. DWM+EXTEND+OPAQUE—— 同上，但窗口用不透明底（Qt 不设 TranslucentBackground）
    4. MICA             —— SYSTEMBACKDROP_TYPE(2)（取桌面壁纸，最大化也可见）

每个窗口左上角写方案名；请找出**客户区整体模糊**的那一个。

用法::

    python scripts/probe-glass-native.py             # 显示四个窗口
    python scripts/probe-glass-native.py --auto 25    # 25 秒后自动退出
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

# ---------------------------------------------------------------- Win32 声明

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_MICA_EFFECT_LEGACY = 1029          # 旧版 Win11 的 Mica 开关

WCA_ACCENT_POLICY = 19

ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
ACCENT_ENABLE_HOSTBACKDROP = 5


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", ctypes.c_int),
    ]


class WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def set_accent(hwnd: int, state: int, *, tint: int = 0x00000000,
               flags: int = 2) -> bool:
    """SetWindowCompositionAttribute —— 亚克力/模糊。

    ⚠️ 关键经验：``GradientColor`` 的 alpha 很讲究。
    alpha 越大，亚克力越"实"；**alpha 为 0 时反而能看到最强的模糊**。
    """
    try:
        accent = ACCENT_POLICY()
        accent.AccentState = state
        accent.AccentFlags = flags
        accent.GradientColor = tint
        data = WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:  # noqa: BLE001
        return False


def extend_frame(hwnd: int, margin: int = -1) -> bool:
    """把 DWM 帧延伸进客户区 —— 这一步是"客户区也吃到材质"的关键。"""
    try:
        m = MARGINS(margin, margin, margin, margin)
        return ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
            wintypes.HWND(hwnd), ctypes.byref(m)) == 0
    except Exception:  # noqa: BLE001
        return False


def set_backdrop(hwnd: int, kind: int, *, dark: bool = True) -> bool:
    try:
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(kind)
        r1 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(val), ctypes.sizeof(val))
        flag = ctypes.c_int(1 if dark else 0)
        r2 = dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(flag), ctypes.sizeof(flag))
        return r1 == 0 and r2 == 0
    except Exception:  # noqa: BLE001
        return False


def set_mica_legacy(hwnd: int, on: bool = True) -> bool:
    """旧版 Win11（22000 系）用的 Mica 开关，某些版本上系统材质只认它。"""
    try:
        val = ctypes.c_int(1 if on else 0)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_MICA_EFFECT_LEGACY,
            ctypes.byref(val), ctypes.sizeof(val)) == 0
    except Exception:  # noqa: BLE001
        return False


def windows_build() -> int:
    try:
        class OSVI(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", wintypes.DWORD),
                ("dwMajorVersion", wintypes.DWORD),
                ("dwMinorVersion", wintypes.DWORD),
                ("dwBuildNumber", wintypes.DWORD),
                ("dwPlatformId", wintypes.DWORD),
                ("szCSDVersion", wintypes.WCHAR * 128),
            ]

        info = OSVI()
        info.dwOSVersionInfoSize = ctypes.sizeof(OSVI)
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info))
        return int(info.dwBuildNumber)
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------- 窗口

def build(kind: str):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    win = QWidget()
    win.resize(430, 250)

    if kind == "opaque":
        # 方案 3 用不透明底：让 Qt 自己画实色，材质叠在上面
        win.setStyleSheet("QWidget { background: rgba(20, 22, 28, 255); }")
    else:
        # 其余都要透明窗口，否则 Qt 画的黑底会盖住材质
        win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        win.setStyleSheet("QWidget { background: transparent; }")

    lay = QVBoxLayout(win)
    lay.setContentsMargins(16, 16, 16, 16)
    label = QLabel()
    label.setStyleSheet("color: #FFFFFF; font-size: 13px;")
    label.setWordWrap(True)
    lay.addWidget(label)
    return win, label


PLANS = (
    ("ACCENT_ACRYLIC", "方案1 亚克力(SetWindowCompositionAttribute, alpha=0)"),
    ("DWM_EXTEND", "方案2 DWM材质 + DwmExtendFrameIntoClientArea(-1)"),
    ("DWM_EXTEND_OPAQUE", "方案3 DWM材质 + 延伸帧 + 不透明底"),
    ("MICA_EXTEND", "方案4 Mica + 延伸帧"),
)


def apply_plan(hwnd: int, plan: str) -> str:
    if plan == "ACCENT_ACRYLIC":
        ok = set_accent(hwnd, ACCENT_ENABLE_ACRYLICBLURBEHIND, tint=0x00000000)
        set_accent(hwnd, ACCENT_ENABLE_HOSTBACKDROP, tint=0x00000000)
        return f"accent(acrylic,α=0)={ok}"
    if plan == "DWM_EXTEND":
        ext = extend_frame(hwnd, -1)
        bd = set_backdrop(hwnd, 3)
        return f"extend={ext} backdrop={bd}"
    if plan == "DWM_EXTEND_OPAQUE":
        ext = extend_frame(hwnd, -1)
        bd = set_backdrop(hwnd, 3)
        set_mica_legacy(hwnd, True)
        return f"extend={ext} backdrop={bd} micaLegacy=1"
    if plan == "MICA_EXTEND":
        ext = extend_frame(hwnd, -1)
        bd = set_backdrop(hwnd, 2)
        return f"extend={ext} backdrop(mica)={bd}"
    return "未知方案"


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
    print("=" * 74)
    print("原生毛玻璃四方案对照")
    print("=" * 74)
    print(f"  Windows build: {windows_build()}")
    print(f"  Qt 平台插件  : {app.platformName()}")
    print()
    print("  请找出**整个窗口都模糊**的那一个（而不是只有标题栏一条）：")
    print()

    wins = []
    for i, (plan, title) in enumerate(PLANS):
        win, label = build("opaque" if plan == "DWM_EXTEND_OPAQUE" else "transparent")
        win.setWindowTitle(title)
        win.move(80 + i * 60, 80 + i * 45)
        win.show()
        app.processEvents()
        detail = apply_plan(int(win.winId()), plan)
        label.setText(f"{title}\n\n{detail}\n\n客户区是否模糊？")
        wins.append(win)
        print(f"  {title:44} {detail}")

    print()
    print("  提示：Win+Shift+S 截图后放大看客户区纹理，比肉眼更准。")
    if auto:
        print(f"  （{auto} 秒后自动退出）")
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
