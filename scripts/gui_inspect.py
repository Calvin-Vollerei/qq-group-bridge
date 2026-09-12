#!/usr/bin/env python3
"""GUI 结构检查：程序化导出控件树与几何，验证布局是否正确。

比截图 OCR 更适合验证布局 —— OCR 认不准界面小字，但几何是精确的。

检查项：
  * 每个标签页实际创建了哪些控件
  * 是否存在「零尺寸 / 未映射」的控件（常见于忘记 pack/grid）
  * 是否有控件超出父容器边界
  * 关键控件是否都在（状态胶囊、日志区、按钮、输入框）
  * 各标签页的内容高度是否合理

用法::

    python scripts/gui_inspect.py            # 摘要
    python scripts/gui_inspect.py --tree      # 打印完整控件树
    python scripts/gui_inspect.py --tab 3     # 只看某个标签页（0 起）
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QGB_DATA_DIR", str(ROOT / ".runtime"))

#: 每个标签页至少应该出现的控件类
#:
#: ⚠️ 注意：ttk 控件的 winfo_class() 带 T 前缀（TButton / TEntry / ...），
#: 经典 tk 控件则不带（Text / Canvas / Listbox）。写错会得到一堆假告警。
EXPECTED = {
    "监控": {"Canvas", "Text", "TButton", "TProgressbar"},
    "群与规则": {"Text", "TEntry", "TCombobox", "Treeview", "TButton"},
    "QQ 登录": {"Text", "TEntry", "TButton", "Label"},
    "网盘与凭据": {"TCombobox", "TEntry", "TButton", "TCheckbutton"},
    "高级": {"TEntry", "TButton", "TCheckbutton", "TLabel"},
}


def has_placement(w) -> bool:
    """控件是否被布局管理器管理过。

    ⚠️ 不能只看 ``winfo_manager()``：``grid_remove()`` 之后它会变成空串。
    但被移除的控件仍保留 remembered options，因此 ``grid_info()`` 非空 ——
    用它才能把「按条件隐藏」和「压根忘了放置」区分开。
    """
    for name in ("grid_info", "pack_info", "place_info"):
        try:
            if getattr(w, name)():
                return True
        except Exception:
            continue
    return False


def walk(widget, depth: int = 0, limit: int = 4000):
    """返回控件行列表。

    每行：``(depth, class, name, x, y, w, h, text, is_scroll_content, is_placed)``

    * ``is_scroll_content``：滚动容器内的内容 —— 它们本来就比视口高，
      参与「越界」判断会产生假告警
    * ``is_placed``：是否被 pack/grid/place 管理过；``False`` 且尺寸为 1x1
      说明「创建了但忘了放置」，这是真 bug
    """
    rows: list[tuple] = []

    def visit(w, d: int, scroll: bool, conditional: bool) -> None:
        if len(rows) > limit:
            return
        # 自身标注 + 祖先标注，一起算作「按条件显示」
        effective = conditional or bool(getattr(w, "qgb_conditional", False))
        try:
            text = ""
            try:
                text = str(w.cget("text"))[:60]
            except Exception:
                text = ""
            try:
                placed = has_placement(w)
            except Exception:
                placed = False
            rows.append((
                d,
                w.winfo_class(),
                w.winfo_name(),
                w.winfo_x(),
                w.winfo_y(),
                w.winfo_width(),
                w.winfo_height(),
                text,
                scroll,
                placed,
                effective,
            ))
        except Exception:
            return
        # 画布的直接子控件 = 滚动内容，整棵子树都豁免越界检查
        child_scroll = scroll or w.winfo_class() == "Canvas"
        for child in w.winfo_children():
            visit(child, d + 1, child_scroll, effective)

    visit(widget, depth, False, False)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="GUI 结构检查")
    parser.add_argument("--tree", action="store_true", help="打印完整控件树")
    parser.add_argument("--tab", type=int, default=-1)
    args = parser.parse_args()

    try:
        from qgb.gui.app import MainWindow

        window = MainWindow()
    except Exception:
        print("❌ 主窗口构建失败：", file=sys.stderr)
        traceback.print_exc()
        return 1

    problems: list[str] = []
    try:
        window.update()
        window.update_idletasks()
        window.attributes("-topmost", True)
        window.lift()
        for _ in range(6):
            window.update()
            import time

            time.sleep(0.1)

        tabs = window._tabs
        indices = range(len(tabs)) if args.tab < 0 else [args.tab]

        for index in indices:
            if index >= len(tabs):
                continue
            tab = tabs[index]
            window.notebook.select(index)
            window.update()
            window.update_idletasks()

            rows = walk(tab)
            classes = {r[1] for r in rows}
            visible = [r for r in rows if r[5] > 1 and r[6] > 1]
            zero = [r for r in rows if r[5] <= 1 or r[6] <= 1]

            print("=" * 68)
            print(f"[{index + 1}] {tab.title}　控件数={len(rows)}　可见={len(visible)}　零尺寸={len(zero)}")
            tw, th = tab.winfo_width(), tab.winfo_height()
            print(f"    容器尺寸：{tw}x{th}")

            # 区分「忘记放置」与「有意隐藏」：
            #   qgb_conditional 子树、或布局参数仍在 = 有意隐藏
            #   从未被任何布局管理器管理过 = 真 bug
            unplaced = [r for r in zero if not r[9] and not r[10]]
            hidden = [r for r in zero if r[9] or r[10]]
            if unplaced:
                problems.append(f"{tab.title}: {len(unplaced)} 个控件创建后未放置")
                print(f"    ❌ 未放置（不可见但存在）：{len(unplaced)} 个")
                for r in unplaced[:6]:
                    print(f"        {r[1]}({r[7] or r[2]}) {r[5]}x{r[6]}")
            else:
                print("    ✅ 无未放置控件")
            if hidden:
                print(f"    ℹ 有意隐藏（如按上传方式切换）：{len(hidden)} 个")

            missing = EXPECTED.get(tab.title, set()) - classes
            if missing:
                problems.append(f"{tab.title}: 缺少控件类型 {sorted(missing)}")
                print(f"    ❌ 缺少预期控件：{sorted(missing)}")
            else:
                print("    ✅ 预期控件齐全")

            # 越界检查（允许 2px 误差）：
            #   * 跳过第 0 行（标签页自身，它不是自己的子控件）
            #   * 跳过滚动内容（本来就比视口高）
            overflow = [
                r for r in visible[1:]
                if not r[8] and (r[3] + r[5] > tw + 2 or r[4] + r[6] > th + 2)
            ]
            if overflow:
                problems.append(f"{tab.title}: {len(overflow)} 个控件越界")
                print(f"    ⚠ 越界控件 {len(overflow)} 个：")
                for r in overflow[:5]:
                    print(f"        {r[1]}({r[7] or r[2]}) @({r[3]},{r[4]}) {r[5]}x{r[6]}")
            else:
                print("    ✅ 无控件越界")

            if args.tree:
                for row in rows:
                    d, cls, name, x, y, w, h, text = row[:8]
                    scroll, placed, conditional = row[8], row[9], row[10]
                    pad = "  " * d
                    t = f' "{text}"' if text else ""
                    mark = " [滚动内容]" if scroll else ""
                    if conditional:
                        mark += " [按条件显示]"
                    if not placed and not conditional and (w <= 1 or h <= 1):
                        mark += " [未放置!]"
                    print(f"      {pad}{cls}({name}) @({x},{y}) {w}x{h}{t}{mark}")

        # 顶部结构
        print("=" * 68)
        print(f"窗口尺寸：{window.winfo_width()}x{window.winfo_height()}")
        print(f"标签页数：{len(tabs)}　顺序：{[t.title for t in tabs]}")

    finally:
        try:
            window.destroy()
        except Exception:
            pass

    print("=" * 68)
    if problems:
        print(f"❌ 发现 {len(problems)} 个布局问题：")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("✅ 布局检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
