"""Qt 界面冒烟测试：逐个标签页切换并检查是否真的渲染出来。

Tk 版有 ``gui_inspect`` / ``gui_smoke``，Qt 版需要等价物 ——
否则某页构造时抛异常只在用户点到它时才暴露。

检查项（每页）：
* 能构造（工厂不抛异常）
* 有非零尺寸（说明真的布局了，而不是 0x0 隐藏）
* 渲染出的像素不是纯色（说明有内容，而不是空白）

用法::

    python scripts/qt-smoke.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402


def main() -> int:
    use_utf8_console()
    import os

    os.environ.setdefault("QGB_DATA_DIR", tempfile.mkdtemp(prefix="qgb-qtsmoke-"))

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from qgb import paths

    paths.reset_cache()

    from qgb.controller import AppController

    app = QApplication(sys.argv)

    controller = AppController()
    controller.load()

    from qgb.qt.shell import Shell

    shell = Shell(controller)
    shell.resize(1100, 720)
    shell.show()
    shell.start()

    results: list[tuple[str, str, str]] = []

    def check_all() -> None:
        tabs = shell.tabs
        for i in range(tabs.count()):
            title = tabs.tabText(i)
            tabs.setCurrentIndex(i)
            app.processEvents()
            page = tabs.widget(i)
            w, h = page.width(), page.height()
            status = "ok"
            detail = f"{w}x{h}"
            if w < 200 or h < 200:
                status, detail = "fail", f"尺寸过小 {w}x{h}"
            else:
                # ⚠️ 用 grab() 而不是 render()：
                #    QWidget.render() 不会绘制**子控件**（只画自身背景），
                #    实测会把 5 个正常页面全判成"疑似空白"。
                #    grab() 走的是真实合成路径，能拿到完整外观。
                pix = page.grab()
                out = Path(".shots") / f"qt-tab-{i}.png"
                out.parent.mkdir(parents=True, exist_ok=True)
                pix.save(str(out))
                img = pix.toImage()
                iw, ih = img.width(), img.height()
                colors = {img.pixel(x, y)
                          for x in range(0, iw, max(1, iw // 24))
                          for y in range(0, ih, max(1, ih // 24))}
                # 统计非透明像素比例：半透明窗口下"全透明"是正常的，
                # 不能只看颜色种类（那会把透明底判成空白）
                opaque = sum(
                    1 for x in range(0, iw, max(1, iw // 32))
                    for y in range(0, ih, max(1, ih // 32))
                    if (img.pixel(x, y) >> 24) & 0xFF > 8
                )
                # ⚠️ 判据必须包含"**有没有子控件**" ——
                #    踩过的坑：只按"颜色种类"判断时，一个**空壳页面**
                #    （build() 没被调用、子控件数为 0）也会被判成通过，
                #    于是我一路绿灯交付了"打开没有内容"的 exe。
                children = len(page.findChildren(object))
                detail += f" 控件={children} 色数={len(colors)} 不透明样本={opaque}"
                if children < 5:
                    status, detail = "fail", detail + "（页面是空壳：build 未生效）"
                elif len(colors) < 3 and opaque < 5:
                    status, detail = "fail", detail + "（疑似空白）"
                detail += f" → {out.name}"
            results.append((title, status, detail))
            print(f"  [{status.upper():4}] {title:12} {detail}")

        tabs.setCurrentIndex(0)
        QTimer.singleShot(300, finish)

    def finish() -> None:
        bad = [r for r in results if r[1] != "ok"]
        print()
        if bad:
            print(f"❌ {len(bad)}/{len(results)} 个标签页有问题")
        else:
            print(f"✅ 全部 {len(results)} 个标签页渲染正常")
        try:
            shell.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            controller.shutdown()
        except Exception:  # noqa: BLE001
            pass
        app.exit(1 if bad else 0)

    QTimer.singleShot(1200, check_all)
    code = app.exec()
    return int(code or 0)


if __name__ == "__main__":
    raise SystemExit(main())
