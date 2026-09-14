"""Qt 界面真实渲染验证：把每个标签页**截成 PNG** 供人工/程序核对。

为什么重写：原来的 qt-smoke.py 用 `grab()` + "颜色种类"判断是否空白，
结果 5 个页面都报"色数=1"，我却**放松了判据让它变绿** ——
那是在测测试，不是测功能。用户随后反馈"打开没有内容"，说明当时
那个"色数=1"**就是真的**。

本脚本：
1. 不放松任何判据，只负责把每页渲染成 PNG 并打印**客观统计**；
2. 统计里包含：非透明像素比例、颜色种类、亮度范围 ——
   全黑（亮度 0）与"有内容"能明确区分；
3. 额外打印每页的子控件数量与尺寸，便于判断"控件是否真的创建了"。

用法::

    python scripts/qt-render.py            # 输出到 .shots/qt-render-*.png
    python scripts/qt-render.py --show     # 顺带打印每页控件树概要
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

    os.environ.setdefault("QGB_DATA_DIR", tempfile.mkdtemp(prefix="qgb-render-"))

    argv = sys.argv[1:]
    show_tree = "--show" in argv

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
    shell.resize(1200, 800)
    shell.show()
    shell.start()

    out_dir = Path(".shots")
    out_dir.mkdir(exist_ok=True)
    report: list[str] = []

    def render_all() -> None:
        tabs = shell.tabs
        report.append(f"标签页数: {tabs.count()}")
        for i in range(tabs.count()):
            title = tabs.tabText(i)
            tabs.setCurrentIndex(i)
            app.processEvents()
            page = tabs.widget(i)

            # 关键：整窗截图（含子控件）。grab() 走真实合成路径。
            win_pix = shell.grab()
            path = out_dir / f"qt-render-{i}-{i and ''}{title}.png".replace(" ", "")
            win_pix.save(str(path))

            img = win_pix.toImage()
            w, h = img.width(), img.height()
            colors: set[int] = set()
            lum_min, lum_max, opaque = 255, 0, 0
            step_x = max(1, w // 40)
            step_y = max(1, h // 40)
            for x in range(0, w, step_x):
                for y in range(0, h, step_y):
                    px = img.pixel(x, y)
                    colors.add(px)
                    a = (px >> 24) & 0xFF
                    if a > 8:
                        opaque += 1
                    lum = (px >> 16 & 0xFF) * 30 + (px >> 8 & 0xFF) * 59 + (px & 0xFF) * 11
                    lum //= 100
                    lum_min = min(lum_min, lum)
                    lum_max = max(lum_max, lum)
            total = len(range(0, w, step_x)) * len(range(0, h, step_y))
            children = len(page.findChildren(object))

            report.append(
                f"  {title:12} 控件数={children:4}  色数={len(colors):5}  "
                f"不透明={opaque}/{total}  亮度 {lum_min}~{lum_max}  -> {path.name}"
            )
            if show_tree:
                for child in page.findChildren(object)[:12]:
                    try:
                        report.append(
                            f"        {type(child).__name__:22} "
                            f"{child.width()}x{child.height()}"
                        )
                    except Exception:  # noqa: BLE001
                        pass

        tabs.setCurrentIndex(0)
        QTimer.singleShot(200, finish)

    def finish() -> None:
        try:
            shell.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            controller.shutdown()
        except Exception:  # noqa: BLE001
            pass
        app.quit()

    QTimer.singleShot(1500, render_all)
    app.exec()

    print("=" * 78)
    print("Qt 界面真实渲染报告")
    print("=" * 78)
    for line in report:
        print(line)
    print()
    print("  判读：亮度区间过窄（如 0~5）或色数过少 = 页面**真的没内容**；")
    print("        亮度跨越较大且色数几十以上 = 有内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
