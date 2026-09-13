"""毛玻璃窗口效果 + **自动降级**。

三条路线，按可用性自动选择（用户选了"自动降级"）：

===== ============================================ ==========================
路线  机制                                            可用条件
===== ============================================ ==========================
A     DWM ``SYSTEMBACKDROP_TYPE``（Mica/Acrylic）   Windows 11 build ≥ 22621
B     ``SetWindowCompositionAttribute`` 亚克力      Windows 10 build ≥ 17134
C     纯 Qt 自绘半透明（不模糊桌面）                 任何平台
===== ============================================ ==========================

降级策略：能 A 就 A；否则能 B 就 B；再不行用 C。
**视觉不会崩** —— C 路线始终给出"半透明卡片"的观感，只是背后桌面不模糊。

真机验证过的调用结果（Windows 11 build 22631）：A 与 B 的 API 调用都返回成功。
注意：API 调用成功 ≠ 用户一定看到模糊效果（还取决于系统设置、窗口是否被遮挡等），
所以 ``detect()`` 只做**能力探测**，实际观感需人工确认。
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33

#: DWM 背景材质取值
BACKDROP_NONE = 1
BACKDROP_MICA = 2
BACKDROP_ACRYLIC = 3
BACKDROP_TABBED = 4

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

#: Windows 11 22H2 起支持 SYSTEMBACKDROP_TYPE
_BUILD_WIN11_22H2 = 22621
#: Windows 10 1803 起支持亚克力
_BUILD_WIN10_ACRYLIC = 17134


@dataclass(slots=True)
class GlassCapability:
    route: str            # "A" / "B" / "C"
    ok: bool
    build: int
    detail: str = ""

    @property
    def is_native(self) -> bool:
        return self.route in ("A", "B")

    def describe(self) -> str:
        names = {"A": "系统材质（Mica/Acrylic）", "B": "亚克力 API",
                 "C": "自绘半透明（不模糊桌面）"}
        return f"路线 {self.route}：{names.get(self.route, self.route)}"


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),      # AABBGGRR
        ("AnimationId", ctypes.c_int),
    ]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def windows_build() -> int:
    """取真实 Windows build（``GetVersionEx`` 会被兼容性垫片骗，用 ntdll）。"""
    if not _IS_WINDOWS:
        return 0
    try:
        class _OSVI(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", wintypes.DWORD),
                ("dwMajorVersion", wintypes.DWORD),
                ("dwMinorVersion", wintypes.DWORD),
                ("dwBuildNumber", wintypes.DWORD),
                ("dwPlatformId", wintypes.DWORD),
                ("szCSDVersion", wintypes.WCHAR * 128),
            ]

        info = _OSVI()
        info.dwOSVersionInfoSize = ctypes.sizeof(_OSVI)
        ctypes.windll.ntdll.RtlGetVersion(ctypes.byref(info))
        return int(info.dwBuildNumber)
    except Exception:  # noqa: BLE001
        return 0


def detect() -> GlassCapability:
    """探测当前系统支持哪条路线 —— **只探测，不改窗口**。"""
    if not _IS_WINDOWS:
        return GlassCapability("C", True, 0, "非 Windows，使用自绘半透明")
    build = windows_build()
    has_dwm = hasattr(ctypes.windll, "dwmapi")
    has_comp = hasattr(ctypes.windll.user32, "SetWindowCompositionAttribute")
    if build >= _BUILD_WIN11_22H2 and has_dwm:
        return GlassCapability("A", True, build, "支持 DWM 系统背景材质")
    if build >= _BUILD_WIN10_ACRYLIC and has_comp:
        return GlassCapability("B", True, build, "支持亚克力 API")
    return GlassCapability("C", True, build, "系统不支持原生材质，使用自绘半透明")


def apply_route_a(hwnd: int, *, dark: bool, kind: int = BACKDROP_ACRYLIC) -> bool:
    """路线 A：DWM 系统背景材质。"""
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
        corner = ctypes.c_int(2)          # 2 = 圆角
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner), ctypes.sizeof(corner))
        ok = (r1 == 0 and r2 == 0)
        if not ok:
            log.debug("DWM 材质部分失败：backdrop=%s dark=%s", r1, r2)
        return ok
    except Exception as exc:  # noqa: BLE001
        log.debug("路线 A 失败：%s", exc)
        return False


def apply_route_b(hwnd: int, *, dark: bool) -> bool:
    """路线 B：亚克力 API。"""
    try:
        tint = 0xCC14161C if dark else 0xCCF2F4F8      # AABBGGRR
        accent = _ACCENT_POLICY()
        accent.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
        accent.AccentFlags = 2
        accent.GradientColor = tint
        data = _WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        return bool(ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception as exc:  # noqa: BLE001
        log.debug("路线 B 失败：%s", exc)
        return False


def clear(hwnd: int) -> None:
    """清掉原生材质（切到路线 C 时必须清，否则会残留半透明底色）。"""
    if not _IS_WINDOWS:
        return
    try:
        accent = _ACCENT_POLICY()
        accent.AccentState = ACCENT_DISABLED
        data = _WINCOMPATTRDATA()
        data.Attribute = WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)
        ctypes.windll.user32.SetWindowCompositionAttribute(
            wintypes.HWND(hwnd), ctypes.byref(data))
    except Exception:  # noqa: BLE001
        pass
    try:
        backdrop = ctypes.c_int(BACKDROP_NONE)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    except Exception:  # noqa: BLE001
        pass


def apply(hwnd: int, cap: GlassCapability, *, dark: bool) -> bool:
    """按探测结果应用材质；A 失败时**自动退回 B、再退回 C**。"""
    if cap.route == "A":
        if apply_route_a(hwnd, dark=dark):
            return True
        log.info("路线 A 应用失败，退回路线 B")
    if cap.route in ("A", "B"):
        if apply_route_b(hwnd, dark=dark):
            return True
        log.info("路线 B 应用失败，退回路线 C（自绘半透明）")
    clear(hwnd)
    return False
