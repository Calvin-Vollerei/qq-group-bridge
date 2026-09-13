"""模糊参数：照抄 pywinstyles 里**实测生效**的调用序列（不要自己发明组合）。

第一手来源（``pywinstyles.apply_style`` 源码，版本 1.8）::

    # style == "aero"   ← 用户实测：模糊生效
    paint(window)
    ChangeDWMAccent(HWND, 30, 2)
    ChangeDWMAccent(HWND, 19, 3, color=0x000000)

    # style == "transparent"  ← 用户实测：模糊生效
    paint(window)
    ChangeDWMAccent(HWND, 30, 2)
    ChangeDWMAccent(HWND, 19, 4, color=0)

其中 ``ChangeDWMAccent`` 就是把 ``SetWindowCompositionAttribute`` 包一层，
**关键在属性号**（这里用 30 与 19，而不是我以为的固定 19），
并且是**连续两次调用** —— 我之前只做单次、且没走属性 30，所以客户区一直是纯色块。

本模块的职责就是把这套"已经验证过能出模糊"的序列固化下来，
并给出两个可选强度（blur / acrylic）。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import POINTER, byref, c_int, sizeof, wintypes

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

#: 属性号（来自 pywinstyles 的实测用法）
ATTR_BACKDROP = 30
ATTR_ACCENT = 19

#: AccentState 取值
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3        # "aero" 用的，模糊生效
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4  # "transparent" 用的，模糊生效


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", c_int),
        ("AccentFlags", c_int),
        ("GradientColor", ctypes.c_uint),
        ("AnimationId", c_int),
    ]


class _WINDOW_COMPOSITION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Attribute", c_int),
        ("Data", POINTER(_ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def _change_accent(hwnd: int, attrib: int, state: int,
                   color: int | None = None, flags: int = 0) -> bool:
    """与 pywinstyles.ChangeDWMAccent 等价的调用。

    ``color`` 为 None 时保持结构体默认值（0），与它的行为一致。
    """
    if not _IS_WINDOWS:
        return False
    try:
        accent = _ACCENT_POLICY()
        accent.AccentState = int(state)
        accent.AccentFlags = int(flags)
        if color is not None:
            accent.GradientColor = int(color)

        data = _WINDOW_COMPOSITION_ATTRIBUTES()
        data.Attribute = int(attrib)
        data.SizeOfData = sizeof(accent)
        data.Data = ctypes.pointer(accent)

        ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), byref(data))
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("ChangeDWMAccent(%s, %s) 失败：%s", attrib, state, exc)
        return False


def apply_blur(hwnd: int) -> bool:
    """「aero」序列：AccentState=3（BLURBEHIND）。用户实测模糊生效。"""
    ok1 = _change_accent(hwnd, ATTR_BACKDROP, 2)
    ok2 = _change_accent(hwnd, ATTR_ACCENT, ACCENT_ENABLE_BLURBEHIND, color=0x000000)
    return ok1 and ok2


def apply_acrylic(hwnd: int) -> bool:
    """「transparent」序列：AccentState=4（ACRYLICBLURBEHIND），color=0。

    用户实测这一档也是模糊的，而且比 aero 更"清透"（aero 偏灰）。
    """
    ok1 = _change_accent(hwnd, ATTR_BACKDROP, 2)
    ok2 = _change_accent(hwnd, ATTR_ACCENT, ACCENT_ENABLE_ACRYLICBLURBEHIND, color=0)
    return ok1 and ok2


def clear(hwnd: int) -> None:
    """还原：把两个属性都关掉。"""
    _change_accent(hwnd, ATTR_ACCENT, ACCENT_DISABLED)
    _change_accent(hwnd, ATTR_BACKDROP, 0)


#: 可选样式（按"效果强度/观感"排序，供界面下拉选择）
STYLES: dict[str, tuple[str, object]] = {
    "acrylic": ("亚克力（清透模糊）", apply_acrylic),
    "blur": ("老式模糊（偏灰）", apply_blur),
    "none": ("无模糊（仅半透明）", clear),
}
DEFAULT_STYLE = "acrylic"


def apply_style(hwnd: int, style: str) -> bool:
    _, fn = STYLES.get(style, STYLES[DEFAULT_STYLE])
    return bool(fn(hwnd))
