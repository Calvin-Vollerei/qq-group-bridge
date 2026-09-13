"""找出真正生效的模糊参数 —— 按用户实测反馈收敛。

用户实测（比我截图分析可靠）：在 pywinstyles 的对照里
**``transparent`` 与 ``aero`` 这两个实现了模糊**。

这修正了我一个错误判据：我拿"客户区是不是纯色块"当"有没有模糊"的证据 ——
但**模糊本身就会把桌面糊成均匀色块**，所以那个判据反过来才是对的。

本探针做两件事：
1. 打印 ``pywinstyles.apply_style`` 对各种 style 实际调用的 Win32 参数
   （直接读它的源码，不猜）；
2. 用**这些精确参数**各开一个窗口，并额外试几种 tint/AccentFlags 组合，
   找出"模糊最明显且不干扰使用"的那一组。

用法::

    python scripts/probe-glass-params.py                 # 看参数 + 开对照窗口
    python scripts/probe-glass-params.py --dump-only      # 只打印参数表
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402


def dump_pywinstyles_params() -> None:
    """把 pywinstyles 内部真正用的参数打印出来 —— 这是"生效方案"的第一手来源。"""
    import pywinstyles

    print("=" * 76)
    print("pywinstyles 内部实际使用的 Win32 参数")
    print("=" * 76)
    src = inspect.getsource(pywinstyles.apply_style)
    for i, line in enumerate(src.splitlines(), 1):
        print(f"  {i:3}: {line.rstrip()}")
    print()

    # 它用到的常量定义
    for name in ("py_win_style", "ChangeDWMAccent", "set_opacity"):
        try:
            obj = getattr(pywinstyles, name)
            s = inspect.getsource(obj)
            if len(s) < 1200:
                print(f"  --- {name} ---")
                for line in s.splitlines():
                    print(f"      {line.rstrip()}")
                print()
        except Exception:  # noqa: BLE001
            pass


#: 用户实测生效的两种：aero（老式模糊）与 transparent（真透明）。
#: 这里把它们对应的 AccentState 都试一遍，并加上 tint 变体。
COMBOS = (
    # (标签, AccentState, AccentFlags, GradientColor)
    ("aero(3, flags=2, α=0)", 3, 2, 0x00000000),
    ("aero(3, flags=0, α=0)", 3, 0, 0x00000000),
    ("blur(3, flags=2, α=0x99黑)", 3, 2, 0x99000000),
    ("acrylic(4, flags=2, α=0)", 4, 2, 0x00000000),
    ("acrylic(4, flags=2, α=0x66黑)", 4, 2, 0x66000000),
    ("hostbackdrop(5, flags=2, α=0)", 5, 2, 0x00000000),
    ("对照: 纯透明(0)", 0, 0, 0x00000000),
)


def build_window(label: str):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    win = QWidget()
    win.resize(380, 190)
    win.setWindowTitle(label)
    # 不设 FramelessWindowHint：用户看到生效的正是这种"有边框"的窗口
    win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    win.setStyleSheet("QWidget { background: transparent; }")

    lay = QVBoxLayout(win)
    lay.setContentsMargins(12, 12, 12, 12)
    text = QLabel(f"{label}\n\n看背后内容是模糊还是清晰")
    text.setStyleSheet("color: #FFFFFF; font-size: 13px;")
    text.setWordWrap(True)
    lay.addWidget(text)
    return win


def main() -> int:
    use_utf8_console()
    dump_pywinstyles_params()

    if "--dump-only" in sys.argv:
        return 0

    import ctypes
    from ctypes import wintypes

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from qgb.qt.glass import (
        WCA_ACCENT_POLICY,
        _ACCENT_POLICY,
        _WINCOMPATTRDATA,
    )

    def set_accent(hwnd: int, state: int, *, tint: int = 0, flags: int = 2) -> bool:
        """SetWindowCompositionAttribute —— 直接调，便于逐个参数对照。"""
        try:
            accent = _ACCENT_POLICY()
            accent.AccentState = int(state)
            accent.AccentFlags = int(flags)
            accent.GradientColor = int(tint)
            data = _WINCOMPATTRDATA()
            data.Attribute = WCA_ACCENT_POLICY
            data.Data = ctypes.pointer(accent)
            data.SizeOfData = ctypes.sizeof(accent)
            return bool(ctypes.windll.user32.SetWindowCompositionAttribute(
                wintypes.HWND(hwnd), ctypes.byref(data)))
        except Exception:  # noqa: BLE001
            return False

    app = QApplication(sys.argv)
    print("=" * 76)
    print("模糊参数对照（请指出哪几个真正模糊）")
    print("=" * 76)

    wins = []
    for i, (label, state, flags, tint) in enumerate(COMBOS):
        win = build_window(label)
        win.move(60 + i * 55, 60 + i * 42)
        win.show()
        app.processEvents()
        ok = set_accent(int(win.winId()), state, tint=tint, flags=flags)
        print(f"  {label:30} AccentState={state} flags={flags} "
              f"tint=0x{tint:08X}  调用={'成功' if ok else '失败'}")
        wins.append(win)

    print()
    print("  请告诉我哪几行真正实现了模糊（背景被糊掉、看不出细节）。")
    auto = 0
    if "--auto" in sys.argv:
        i = sys.argv.index("--auto")
        auto = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 0
    if auto:
        QTimer.singleShot(auto * 1000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
