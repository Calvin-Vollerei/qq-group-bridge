"""生成应用图标（QQ群文件搬运工）。

设计思路：
  · 蓝色渐变圆角底 —— 稳重、在深色/浅色任务栏上都亮眼；
  · 白色文件夹轮廓 —— "群文件"的直觉符号；
  · 向下的箭头 —— "搬运/下载到网盘"的动作；
  · 16px 下只保留色块与箭头轮廓，所以线条都画得粗、留白大。

输出：
  qgb/assets/icon.png    256×256（窗口/关于页用）
  qgb/assets/icon.ico    多尺寸（Windows exe/任务栏用）

用法::

    python scripts/make-icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402

SIZE = 256
#: ICO 里包含的尺寸（Windows 各场景会挑用）
ICO_SIZES = [256, 128, 64, 48, 32, 24, 16]

# 配色：蓝→青渐变，和界面强调色一致
TOP = (56, 132, 255)
BOTTOM = (26, 88, 190)
FOLDER = (255, 255, 255)
ARROW = (26, 88, 190)


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def build(size: int = SIZE):
    """画一张图标（返回 RGBA 图像）。"""
    from PIL import Image, ImageDraw

    S = size
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # ---- 圆角底：逐行画渐变，再套圆角遮罩
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / max(1, S - 1)
        gd.line([(0, y), (S, y)],
                fill=(_lerp(TOP[0], BOTTOM[0], t),
                      _lerp(TOP[1], BOTTOM[1], t),
                      _lerp(TOP[2], BOTTOM[2], t), 255))
    radius = int(S * 0.22)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    # ---- 文件夹（白色）：底 + 上方的"标签"
    pad = int(S * 0.20)
    fw = S - pad * 2
    tab_w = int(fw * 0.42)
    tab_h = int(S * 0.075)
    body_top = pad + tab_h
    d.rounded_rectangle(
        [pad, pad + int(tab_h * 0.4), pad + tab_w, body_top + int(tab_h * 0.5)],
        radius=int(S * 0.03), fill=FOLDER)
    d.rounded_rectangle(
        [pad, body_top, pad + fw, S - pad - int(S * 0.06)],
        radius=int(S * 0.055), fill=FOLDER)

    # ---- 向下箭头（挖空在文件夹上，用底色描出对比）
    cx = pad + fw // 2
    arrow_top = body_top + int(S * 0.075)
    arrow_bot = S - pad - int(S * 0.09)
    head = int(S * 0.10)
    stem = max(2, int(S * 0.055))
    d.polygon([
        (cx - head, arrow_bot - head), (cx + head, arrow_bot - head), (cx, arrow_bot)
    ], fill=ARROW)
    d.rectangle(
        [cx - stem // 2, arrow_top, cx + stem // 2, arrow_bot - head + 1],
        fill=ARROW)

    return img


def main() -> int:
    use_utf8_console()
    out = ROOT / "qgb" / "assets"
    out.mkdir(parents=True, exist_ok=True)

    big = build(SIZE)
    png = out / "icon.png"
    big.save(png)
    print(f"  ✅ {png.relative_to(ROOT)}  {SIZE}x{SIZE}  "
          f"{png.stat().st_size / 1024:.1f} KB")

    ico = out / "icon.ico"
    # PIL 会按 sizes 逐档缩放并打包
    big.save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    print(f"  ✅ {ico.relative_to(ROOT)}  含尺寸 {ICO_SIZES}  "
          f"{ico.stat().st_size / 1024:.1f} KB")

    # 顺手导出 16px 预览，方便肉眼确认小尺寸是否还看得清
    prev = ROOT / ".shots" / "icon-preview.png"
    prev.parent.mkdir(exist_ok=True)
    strip = build(16).resize((64, 64), 0)  # NEAREST：看真实像素
    strip.save(prev)
    print(f"  ✅ 16px 放大预览（最近邻）: {prev}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
