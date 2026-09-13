"""自绘毛玻璃背景：截取窗口背后的内容 → 高斯模糊 → 作为窗口背景绘制。

为什么必须自己画（踩坑记录）：

用户实测两次反馈「只有最顶部一条是模糊，主页面还是黑的」。查下来：
* 那条模糊的是**标题栏**，由 DWM 绘制，材质确实生效了；
* 而**客户区**是 Qt 绘制的 —— Qt 在 Windows 上**无法把 DWM 材质透上来**
  （客户区最终被填成黑色）。改 QSS 的透明度、去掉控件实底、关闭
  autoFillBackground 全都没用，因为问题不在"有没有实底"，而在
  "客户区根本没有可透的合成层"。

所以改成**自绘**：用 ``QScreen.grabWindow`` 取窗口背后的屏幕像素，
缩放到 1/6 后高斯模糊，再作为窗口背景拉伸绘制。好处：

* 客户区**真的有模糊内容**，不依赖 DWM/Qt 的合成；
* 跨平台一致（Win10 也有同样效果）；
* 模糊强度、暗化程度都可调。

代价：它是"桌面快照"，不是实时合成 —— 窗口**移动/缩放后需要重新截图**
（本模块用防抖定时器自动处理）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)

#: 截图后先缩到这个比例再模糊 —— 既省算力，模糊本身也更自然
DOWNSCALE = 6
#: 移动/缩放停止后多久重新截图（毫秒）
_REFRESH_DEBOUNCE_MS = 180
#: 兜底刷新间隔：桌面可能自己变化（换壁纸、别的窗口动了）
_REFRESH_MAX_MS = 1500


def blur_pixmap(pixmap: QPixmap, radius: float = 14.0,
                downscale: int = DOWNSCALE) -> QPixmap:
    """对图片做高斯模糊。

    做法：缩到 1/``downscale`` → 用平滑缩放反复缩放着色器做近似模糊 → 放回原尺寸。
    用"缩小再放大"来近似高斯，是因为它比逐像素卷积快一到两个数量级，
    而观感差异在毛玻璃这种场景里可以忽略。
    """
    if pixmap.isNull():
        return pixmap
    w = max(1, pixmap.width() // max(1, downscale))
    h = max(1, pixmap.height() // max(1, downscale))
    small = pixmap.scaled(
        w, h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    # 反复缩放 2~3 次，平滑插值会累积成接近高斯的效果
    for _ in range(3):
        sw = max(1, small.width() // 2)
        sh = max(1, small.height() // 2)
        small = small.scaled(sw, sh, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        small = small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return small.scaled(
        pixmap.width(), pixmap.height(),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


class BackdropPainter(QObject):
    """给一个窗口提供"模糊背景"。

    用法::

        backdrop = BackdropPainter(window, blur=16, darken=0.55)
        window.installEventFilter(backdrop)   # 自动在移动/缩放后重拍
        # 在窗口的 paintEvent 里：
        backdrop.paint(painter)

    也可以不装事件过滤器，直接 ``backdrop.refresh()`` 手动刷新。
    """

    def __init__(self, window: QWidget, *, blur: float = 16.0,
                 darken: float = 0.55, tint: QColor | None = None,
                 downscale: int = DOWNSCALE) -> None:
        super().__init__(window)
        self.window = window
        self.blur = blur
        self.darken = max(0.0, min(0.95, darken))
        self.tint = tint
        self.downscale = downscale
        self._pixmap: QPixmap | None = None

        # 防抖：拖动时不要每像素都重拍
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_REFRESH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.refresh)

        # 兜底：桌面自身变化（换壁纸、背后窗口动了）
        self._keepalive = QTimer(self)
        self._keepalive.setInterval(_REFRESH_MAX_MS)
        self._keepalive.timeout.connect(self.refresh)

    # -------------------------------------------------- 生命周期

    def start(self) -> None:
        self.refresh()
        self._keepalive.start()

    def stop(self) -> None:
        self._keepalive.stop()
        self._debounce.stop()

    # -------------------------------------------------- 刷新

    def schedule_refresh(self) -> None:
        self._debounce.start()

    def refresh(self) -> None:
        """重拍窗口背后的屏幕内容并模糊。"""
        win = self.window
        try:
            screen = win.screen()
            if screen is None:
                return
            geo = win.frameGeometry()
            # 取窗口在屏幕上的区域（含边框），按设备像素比换算
            dpr = float(win.devicePixelRatioF() or 1.0)
            rect = QRect(int(geo.x() * dpr), int(geo.y() * dpr),
                         max(1, int(geo.width() * dpr)),
                         max(1, int(geo.height() * dpr)))
            shot = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
            if shot.isNull():
                return
            shot = shot.scaled(
                max(1, int(win.width())), max(1, int(win.height())),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._pixmap = blur_pixmap(shot, self.blur, self.downscale)
            win.update()
        except Exception:  # noqa: BLE001 - 截图失败不该影响界面
            log.debug("截取背景失败", exc_info=True)

    # -------------------------------------------------- 绘制

    def paint(self, painter: QPainter) -> None:
        """把模糊背景铺满整个窗口（由窗口的 paintEvent 调用）。"""
        rect = self.window.rect()
        if self._pixmap is None or self._pixmap.isNull():
            # 还没拍到：用中性色兜底，避免出现纯黑
            painter.fillRect(rect, QColor(28, 30, 38, 235))
            return
        painter.drawPixmap(rect, self._pixmap)
        if self.darken > 0:
            overlay = QColor(12, 14, 20)
            overlay.setAlphaF(self.darken)
            painter.fillRect(rect, overlay)
        if self.tint is not None:
            painter.fillRect(rect, self.tint)

    @property
    def has_backdrop(self) -> bool:
        return self._pixmap is not None and not self._pixmap.isNull()

    # -------------------------------------------------- 事件监听

    def eventFilter(self, obj: QObject, event) -> bool:  # noqa: N802
        from PySide6.QtCore import QEvent

        t = event.type()
        if t in (QEvent.Type.Move, QEvent.Type.Resize):
            self.schedule_refresh()
        elif t == QEvent.Type.Show:
            self.refresh()
        return False          # 只观察，不拦截
