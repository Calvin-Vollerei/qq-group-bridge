"""界面主题：配色、字体、ttk 样式、高 DPI 适配。

配色刻意沿用项目主视觉（深蓝 + 青绿），让「工具」和「交付的 PPT」看起来
是同一个体系；同时用浅色底，降低部署方的心理门槛。
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

__all__ = ["Palette", "apply_theme", "enable_dpi_awareness", "mono_font", "ui_font"]


class Palette:
    """集中管理颜色，避免色值散落各处。"""

    BG = "#F5F7FA"          # 窗口底
    CARD = "#FFFFFF"        # 卡片
    CARD_ALT = "#EEF3F8"    # 次级卡片
    BORDER = "#D7DEE6"      # 分隔线/描边
    PRIMARY = "#0F4C81"     # 主色
    PRIMARY_DARK = "#0B3A63"
    ACCENT = "#2AA198"      # 强调（青绿）
    WARN = "#D97706"        # 警告
    DANGER = "#C0392B"      # 危险
    TEXT = "#263238"        # 正文
    MUTED = "#6B7480"       # 次要文字
    DISABLED = "#A9B3BE"

    # 日志级别配色
    LOG = {
        "debug": MUTED,
        "info": TEXT,
        "warning": WARN,
        "error": DANGER,
        "success": "#1E7A5F",
        "muted": MUTED,
    }

    # 状态胶囊
    STATE = {
        "running": (ACCENT, "#FFFFFF"),
        "paused": (WARN, "#FFFFFF"),
        "stopped": ("#8A94A0", "#FFFFFF"),
        "waiting": (DANGER, "#FFFFFF"),
    }


def enable_dpi_awareness() -> float:
    """开启 Windows 高 DPI 感知，避免界面发虚。

    返回缩放比例（1.0 表示未缩放），供字体统一放大使用。
    """
    if sys.platform != "win32":
        return 1.0
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        return 1.0

    try:
        import ctypes

        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return max(1.0, dpi / 96.0)
    except Exception:
        pass
    return 1.0


def ui_font(scale: float = 1.0, *, size: int = 10, bold: bool = False) -> tuple:
    family = "Microsoft YaHei UI" if sys.platform == "win32" else "Helvetica"
    return (family, max(8, int(round(size * scale))), "bold" if bold else "normal")


def mono_font(scale: float = 1.0, *, size: int = 9) -> tuple:
    family = "Consolas" if sys.platform == "win32" else "Courier"
    return (family, max(8, int(round(size * scale))))


def apply_theme(root: tk.Misc, scale: float = 1.0) -> ttk.Style:
    """配置 ttk 样式。返回 Style 对象，便于局部覆盖。"""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # clam 才允许自由改底色
    except tk.TclError:
        pass

    base_font = ui_font(scale, size=10)
    title_font = ui_font(scale, size=13, bold=True)
    head_font = ui_font(scale, size=11, bold=True)

    root.configure(bg=Palette.BG)

    style.configure(".", background=Palette.BG, foreground=Palette.TEXT, font=base_font)
    style.configure("TFrame", background=Palette.BG)
    style.configure("Card.TFrame", background=Palette.CARD, relief="flat")
    style.configure("Alt.TFrame", background=Palette.CARD_ALT)

    style.configure("TLabel", background=Palette.BG, foreground=Palette.TEXT, font=base_font)
    style.configure("Muted.TLabel", background=Palette.BG, foreground=Palette.MUTED,
                    font=ui_font(scale, size=9))
    style.configure("Card.TLabel", background=Palette.CARD, foreground=Palette.TEXT)
    style.configure("CardMuted.TLabel", background=Palette.CARD, foreground=Palette.MUTED,
                    font=ui_font(scale, size=9))
    style.configure("Title.TLabel", background=Palette.BG, foreground=Palette.PRIMARY,
                    font=title_font)
    style.configure("Head.TLabel", background=Palette.BG, foreground=Palette.PRIMARY,
                    font=head_font)
    style.configure("CardHead.TLabel", background=Palette.CARD, foreground=Palette.PRIMARY,
                    font=head_font)
    style.configure("Danger.TLabel", background=Palette.BG, foreground=Palette.DANGER)
    style.configure("Warn.TLabel", background=Palette.BG, foreground=Palette.WARN)
    style.configure("Ok.TLabel", background=Palette.BG, foreground="#1E7A5F")

    style.configure("TLabelframe", background=Palette.BG, bordercolor=Palette.BORDER,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=Palette.BG, foreground=Palette.PRIMARY,
                    font=head_font)

    style.configure("TButton", padding=(12, 6), font=base_font)
    style.configure("Accent.TButton", padding=(14, 7), foreground="#FFFFFF",
                    background=Palette.PRIMARY, font=ui_font(scale, size=10, bold=True))
    style.map(
        "Accent.TButton",
        background=[("active", Palette.PRIMARY_DARK), ("disabled", Palette.DISABLED)],
        foreground=[("disabled", "#E8EEF5")],
    )
    style.configure("Ghost.TButton", padding=(10, 5))
    style.configure("Danger.TButton", padding=(12, 6), foreground="#FFFFFF",
                    background=Palette.DANGER)
    style.map("Danger.TButton", background=[("active", "#96261A")])

    style.configure("TEntry", fieldbackground="#FFFFFF", bordercolor=Palette.BORDER,
                    padding=4)
    style.configure("TCombobox", fieldbackground="#FFFFFF", padding=4)
    style.configure("TCheckbutton", background=Palette.BG, font=base_font)
    style.configure("Card.TCheckbutton", background=Palette.CARD, font=base_font)

    style.configure("TNotebook", background=Palette.BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(18, 9), font=ui_font(scale, size=10))
    style.map(
        "TNotebook.Tab",
        background=[("selected", Palette.CARD), ("!selected", Palette.CARD_ALT)],
        foreground=[("selected", Palette.PRIMARY), ("!selected", Palette.MUTED)],
    )

    style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                    rowheight=int(24 * scale), font=ui_font(scale, size=9))
    style.configure("Treeview.Heading", font=ui_font(scale, size=9, bold=True),
                    background=Palette.CARD_ALT, foreground=Palette.PRIMARY)

    style.configure("Horizontal.TProgressbar", background=Palette.ACCENT,
                    troughcolor=Palette.CARD_ALT, borderwidth=0)

    style.configure("TSeparator", background=Palette.BORDER)

    # 让默认字体也跟着缩放（Text/Listbox 等非 ttk 控件用）
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=base_font[0], size=base_font[1])
        except tk.TclError:
            pass

    return style
