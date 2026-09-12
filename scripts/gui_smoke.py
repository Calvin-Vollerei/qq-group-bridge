#!/usr/bin/env python3
"""GUI 渲染冒烟：把每个标签页截图存盘，用于人工/自动核对界面。

为什么需要它：tkinter 的错误只在真正创建控件时才暴露
（例如给控件属性起名 ``_w`` 会覆盖 tkinter 内部的控件路径名），
所以「能 import」不等于「能渲染」。本脚本强制走一遍真实渲染路径。

用法::

    python scripts/gui_smoke.py                 # 截全部标签页
    python scripts/gui_smoke.py --tab 2         # 只截第 3 个标签页
    python scripts/gui_smoke.py --out .shots    # 指定输出目录

退出码非零表示界面构建失败（异常会完整打印）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from _console import use_utf8_console  # noqa: E402  控制台切 UTF-8（Linux 上常是 cp1252）
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _prepare_env() -> None:
    """默认把数据目录放到项目内，避免污染真实用户数据。"""
    os.environ.setdefault("QGB_DATA_DIR", str(ROOT / ".runtime"))


def pixel_values(image):
    """兼容新旧 Pillow：``getdata()`` 在 Pillow 14 起被弃用。"""
    getter = getattr(image, "get_flattened_data", None) or image.getdata
    return getter()


def main() -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="GUI 渲染冒烟")
    parser.add_argument("--out", default=str(ROOT / ".shots"), help="截图输出目录")
    parser.add_argument("--tab", type=int, default=-1, help="只截某个标签页（0 起）")
    parser.add_argument("--keep-open", type=float, default=0.0,
                        help="截完后保持窗口 N 秒（便于人工查看）")
    args = parser.parse_args()

    _prepare_env()

    try:
        from PIL import ImageGrab
    except ImportError:
        print("需要 Pillow：pip install Pillow", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print("GUI 渲染冒烟")
    print("=" * 64)

    try:
        from qgb.gui.app import MainWindow

        window = MainWindow()
    except Exception:
        print("❌ 主窗口构建失败：", file=sys.stderr)
        traceback.print_exc()
        return 1

    results: list[tuple[str, bool, str]] = []
    try:
        window.attributes("-topmost", True)
        window.lift()
        window.update()
        window.update_idletasks()
        time.sleep(0.6)

        # 等事件泵跑几轮，让状态/统计落到界面上
        for _ in range(6):
            window.update()
            time.sleep(0.12)

        tabs = window._tabs
        indices = range(len(tabs)) if args.tab < 0 else [args.tab]

        for index in indices:
            if index >= len(tabs):
                continue
            tab = tabs[index]
            try:
                window.notebook.select(index)
                window.update()
                window.update_idletasks()
                time.sleep(0.45)
                window.update()

                x = window.winfo_rootx()
                y = window.winfo_rooty()
                w = window.winfo_width()
                h = window.winfo_height()
                shot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
                path = out_dir / f"tab{index + 1}-{tab.title.replace(' ', '')}.png"
                shot.save(path)

                # 健全性判断：缩略后统计不同灰阶数与对比度，
                # 全黑/全白/单一色都说明没真正渲染出内容。
                gray = shot.convert("L")
                low, high = gray.getextrema()
                distinct = len(set(pixel_values(gray.resize((80, 60)))))
                ok = distinct >= 6 and (high - low) > 40 and shot.size[0] > 200
                results.append(
                    (tab.title, ok,
                     f"{shot.size[0]}x{shot.size[1]}, {distinct} 级灰阶, 对比 {high - low} → {path.name}")
                )
            except Exception as exc:
                results.append((tab.title, False, f"{type(exc).__name__}: {exc}"))

        if args.keep_open > 0:
            deadline = time.time() + args.keep_open
            while time.time() < deadline:
                window.update()
                time.sleep(0.05)

    finally:
        try:
            window.destroy()
        except Exception:
            pass

    print()
    for title, ok, detail in results:
        print(f"  {'✅' if ok else '❌'} {title:<12} {detail}")

    passed = sum(1 for _, ok, _ in results if ok)
    print("-" * 64)
    print(f"结果：{passed}/{len(results)} 个标签页渲染正常 → {out_dir}")
    print("=" * 64)
    return 0 if results and passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
