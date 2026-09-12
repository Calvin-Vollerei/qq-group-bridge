#!/usr/bin/env python3
"""验证**打包后的产物**：启动 exe、找到它的窗口、截图并保存，然后关闭。

为什么需要它：
  * 源码能跑 ≠ 打包产物能跑（漏掉的隐式依赖、被 exclude 掉的模块、
    路径变化都会在冻结后才暴露）
  * ``--selftest`` 只能证明"模块都在"，证明不了"窗口能画出来"

用法::

    python scripts/verify_package.py
    python scripts/verify_package.py --exe dist/QQ群文件搬运工/QQGroupBridge.exe
    python scripts/verify_package.py --keep     # 不自动关闭，便于人工查看

退出码非零表示打包产物有问题。
"""

from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER32 = ctypes.WinDLL("user32")


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def windows_for_pid(pid: int) -> list[tuple[int, str, tuple[int, int, int, int]]]:
    """枚举某进程的所有可见顶层窗口：``[(hwnd, title, (l,t,r,b)), ...]``。"""
    found: list[tuple[int, str, tuple[int, int, int, int]]] = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value != pid or not USER32.IsWindowVisible(hwnd):
            return True

        length = USER32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        USER32.GetWindowTextW(hwnd, buf, length + 1)

        rect = wintypes.RECT()
        USER32.GetWindowRect(hwnd, ctypes.byref(rect))
        found.append((int(hwnd), buf.value, (rect.left, rect.top, rect.right, rect.bottom)))
        return True

    USER32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def pixel_values(image):
    """兼容新旧 Pillow：``getdata()`` 在 Pillow 14 起被弃用。"""
    getter = getattr(image, "get_flattened_data", None) or image.getdata
    return getter()


def enabling_guard() -> None:  # 占位以便阅读顺序
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="验证打包产物")
    parser.add_argument("--exe", default=str(ROOT / "dist" / "QQ群文件搬运工" / "QQGroupBridge.exe"))
    parser.add_argument("--out", default=str(ROOT / ".shots" / "packaged-window.png"))
    parser.add_argument("--timeout", type=float, default=25.0, help="等待窗口出现的秒数")
    parser.add_argument("--keep", action="store_true", help="验证后不关闭进程")
    args = parser.parse_args()

    exe = Path(args.exe)
    print("=" * 64)
    print("打包产物验证")
    print("=" * 64)
    print(f"目标：{exe}")

    if not exe.is_file():
        print("❌ 可执行文件不存在（先运行 scripts/build.ps1）")
        return 2

    try:
        from PIL import ImageGrab
    except ImportError:
        print("需要 Pillow：pip install Pillow")
        return 2

    enable_dpi_awareness()

    print("\n[1/4] 启动进程…")
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    print(f"      pid = {proc.pid}")

    print("[2/4] 等待窗口出现…")
    deadline = time.time() + args.timeout
    windows: list[tuple[int, str, tuple[int, int, int, int]]] = []
    while time.time() < deadline:
        if proc.poll() is not None:
            print(f"❌ 进程提前退出（退出码 {proc.returncode}）")
            return 1
        windows = windows_for_pid(proc.pid)
        if windows:
            break
        time.sleep(0.4)

    if not windows:
        print(f"❌ {args.timeout:.0f} 秒内没有出现可见窗口")
        if not args.keep:
            proc.terminate()
        return 1

    hwnd, title, rect = max(windows, key=lambda w: (w[2][2] - w[2][0]) * (w[2][3] - w[2][1]))
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    print(f"      ✅ 窗口已出现：{title!r}  {width}x{height}")

    if width < 200 or height < 150:
        print(f"❌ 窗口尺寸异常：{width}x{height}")
        if not args.keep:
            proc.terminate()
        return 1

    print("[3/4] 截图…")
    time.sleep(1.2)  # 让界面画完
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shot = ImageGrab.grab(bbox=rect)
    shot.save(out)

    # 健全性：缩略后看灰阶数与对比度，避免把"空白窗口"当成成功
    gray = shot.convert("L")
    low, high = gray.getextrema()
    distinct = len(set(pixel_values(gray.resize((80, 60)))))
    print(f"      保存：{out}  （{distinct} 级灰阶，对比度 {high - low}）")

    ok = distinct >= 6 and (high - low) > 40

    print("[4/4] 关闭进程…")
    if not args.keep:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("      已关闭")
    else:
        print("      已保留（--keep）")

    print("-" * 64)
    if ok:
        print("✅ 打包产物验证通过：程序正常启动并渲染出界面")
        return 0
    print("❌ 窗口内容异常（可能是空白窗口）——请人工查看截图")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
