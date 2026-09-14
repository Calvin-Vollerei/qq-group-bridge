"""主窗口：无边框毛玻璃外壳 + 标签页 + 日夜主题切换。

职责边界（便于后续逐页迁移）：

* ``Shell`` 只负责**窗口级**的事：玻璃材质、主题应用、标题栏、标签页容器、
  状态条、事件桥接。各页面只关心自己的内容。
* 页面通过 ``Page`` 基类拿到 ``controller`` / ``bridge`` / ``toast``，
  不直接碰窗口。

为什么无边框：毛玻璃（尤其自绘路线 C）需要窗口自绘圆角与半透明底；
有系统边框时圆角会与系统阴影打架。代价是要自己实现拖动与边缘缩放 ——
见 ``Shell._install_drag_and_resize``。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import blur, glass, themes
from .widgets import LogView, StatusBadge, ToastHost

log = logging.getLogger(__name__)

#: 边缘缩放的热区宽度（像素）
_RESIZE_MARGIN = 6


class EventBridge(QObject):
    """把控制器的轮询式事件转成 Qt 信号（线程安全地投递到界面线程）。

    控制器（``qgb.controller``）是**框架无关**的：它把事件塞进一个队列，
    界面自己 ``drain()``。Tk 版用 ``after`` 轮询；Qt 版同样轮询，但通过信号
    投递，保证槽函数在界面线程执行。
    """

    event = Signal(object)

    def __init__(self, controller, parent: QObject | None = None,
                 interval_ms: int = 120) -> None:
        super().__init__(parent)
        self._controller = controller
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._pump)
        self._stopped = False

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._stopped = True
        self._timer.stop()

    def _pump(self) -> None:
        if self._stopped or self._controller is None:
            return
        try:
            pending = self._controller.drain()
        except Exception:  # noqa: BLE001 - 轮询异常不能把界面带崩
            log.debug("drain 事件失败", exc_info=True)
            return
        for ev in pending:
            self.event.emit(ev)


class Page(QWidget):
    """页面基类：只暴露页面需要的东西。"""

    #: 标签页标题
    title = "页面"

    def __init__(self, shell: "Shell") -> None:
        super().__init__()
        self.shell = shell
        # 用 getattr 兜底：页面构造期间外壳可能还没装好某个部件，
        # 缺一个就不该让整个窗口起不来。
        self.controller = getattr(shell, "controller", None)
        self.bridge = getattr(shell, "bridge", None)
        self.toast = getattr(shell, "toast", None)

    def build(self) -> QWidget:
        """子类实现：返回本页根控件。"""
        raise NotImplementedError

    def on_event(self, event) -> None:  # noqa: BLE001
        """收到控制器事件（默认忽略）。"""

    def on_theme_changed(self, palette: themes.Palette) -> None:
        """主题变化时的额外处理（默认无需处理，QSS 已覆盖大部分）。"""


class Shell(QMainWindow):
    """毛玻璃主窗口。"""

    def __init__(self, controller, *, theme: str | None = None) -> None:
        super().__init__()
        self.controller = controller
        self.theme_key = theme or getattr(
            getattr(getattr(controller, "config", None), "ui", None), "theme", None
        ) or themes.DEFAULT_THEME

        self.capability = glass.detect()      # 只用于能力提示，实际模糊走 blur.py
        self.toast = ToastHost(self)

        # 模糊模式：优先读配置（「高级」页可改），默认亚克力
        ui = getattr(getattr(controller, "config", None), "ui", None)
        style = getattr(ui, "glass_style", None) or blur.DEFAULT_STYLE
        self.glass_style = style if style in blur.STYLES else blur.DEFAULT_STYLE

        self.setWindowTitle("QQ群文件搬运工")
        self.setMinimumSize(880, 560)
        self.resize(1080, 720)

        # ⚠️ 窗口外观由毛玻璃路线决定（见 glass.GlassCapability.requires_frameless）：
        #    原生材质（A/B）必须用**系统边框**且**不设** WA_TranslucentBackground，
        #    否则 Qt 自绘的背景会盖住 DWM 的材质 —— 实测就是"没有毛玻璃、只有半透明块"。
        #    自绘路线（C）才用无边框 + 半透明 + 自绘圆角。
        self.frameless = self.capability.requires_frameless
        if self.frameless:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # ⚠️ 无论哪条路线都要开 WA_TranslucentBackground：
        #    自绘路线需要它来画圆角；原生路线也需要它，因为**客户区**由我们自己
        #    绘制模糊背景（Qt 在 Windows 上无法把 DWM 材质透到客户区，
        #    不开这个属性客户区会被填成纯黑 —— 正是用户看到的"死黑"）。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # ⚠️ bridge 必须在 _build_ui() **之前**创建：页面在构造时就会取它
        #    （Page.__init__ 里 self.bridge = shell.bridge），否则报
        #    AttributeError: 'Shell' object has no attribute 'bridge'。
        #    实测踩到过，所以顺序不能随意调。
        self.bridge = EventBridge(controller, self)
        self.bridge.event.connect(self._on_event)

        self._build_ui()
        if self.frameless:
            # 只有无边框窗口才需要自己实现拖动与边缘缩放；
            # 系统边框模式下安装它反而会抢事件（容易出怪现象）。
            self._install_drag_and_resize()

        self.apply_theme(self.theme_key)
        QTimer.singleShot(0, self._apply_glass)     # 等窗口有 HWND 后再上材质

    # ------------------------------------------------------------ 构建

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        # 原生材质模式下**绝不能**让中央控件画实底：那会盖住 DWM 材质
        # （实测现象：只有最顶部标题栏那条模糊，主内容区是黑的）。
        root.setAutoFillBackground(False)
        # 中央控件必须**完全透明**：窗口模糊是系统画在窗口背后的
        # （见 blur.py），这里一旦有实底就会把模糊盖住 ——
        # 实测踩过：客户区变成"死黑"，只有标题栏那条是模糊的。
        root.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        root.setAutoFillBackground(False)
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        # 窗口背景由我们自己画，这里留 10px 内边距让内容不贴边
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)


        # ---- 标题栏（自绘）
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.title_label = QLabel("QQ群文件搬运工")
        self.title_label.setObjectName("Title")
        bar.addWidget(self.title_label)

        self.glass_badge = StatusBadge()
        bar.addWidget(self.glass_badge)
        self.state_badge = StatusBadge("未启动")
        bar.addWidget(self.state_badge)
        bar.addStretch(1)

        self.btn_theme = QPushButton("主题")
        self.btn_theme.setObjectName("Ghost")
        self.btn_theme.setToolTip("切换夜间 / 日间主题")
        self.btn_theme.clicked.connect(self.toggle_theme)
        bar.addWidget(self.btn_theme)

        # 自绘窗口按钮只在无边框模式下需要；系统边框自带最小化/最大化/关闭，
        # 再放一套既重复、又容易和拖动逻辑抢事件（实测"点一下就隐藏"多半源于此）。
        if self.frameless:
            self.btn_min = QPushButton("—")
            self.btn_min.setObjectName("Ghost")
            self.btn_min.setFixedWidth(38)
            self.btn_min.clicked.connect(self.showMinimized)
            bar.addWidget(self.btn_min)

            self.btn_close = QPushButton("✕")
            self.btn_close.setObjectName("Ghost")
            self.btn_close.setFixedWidth(38)
            self.btn_close.clicked.connect(self.close)
            bar.addWidget(self.btn_close)
        outer.addLayout(bar)

        # ---- 标签页
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        # 标签页容器与每个页面都必须透明，否则会把窗口背后的材质挡住
        if not self.frameless:
            self.tabs.setAutoFillBackground(False)
            self.tabs.setStyleSheet(
                "QTabWidget, QTabWidget::pane, QStackedWidget "
                "{ background: transparent; }"
            )
        outer.addWidget(self.tabs, 1)

        self.pages: list[Page] = []
        for factory in self._page_factories():
            page = factory(self)
            # ⚠️ **必须调用 build() 并把返回的控件挂到页面上。**
            #    踩过的坑：早期只写了 ``self.tabs.addTab(page, ...)``，
            #    build() 从未被调用 —— 于是每个标签页都是一个**空壳**
            #    （子控件数 0、不透明像素 1%），用户看到的是"打开没有内容"。
            #    这就是"源码能跑、界面却是空白"的根因。
            content = page.build()
            if content is not None:
                # 注意：不要在函数内 import QVBoxLayout —— 那会让 Python 把整个
                # 函数作用域里的该名字视为局部变量，导致上面的 QVBoxLayout(root)
                # 报 UnboundLocalError（实测踩到过）。模块顶部已导入。
                lay = page.layout() or QVBoxLayout(page)
                lay.setContentsMargins(0, 0, 0, 0)
                lay.setSpacing(0)
                lay.addWidget(content)
            self.pages.append(page)
            self.tabs.addTab(page, page.title)

        # ---- 底部状态条
        foot = QHBoxLayout()
        foot.setSpacing(10)
        self.footer = QLabel("")
        self.footer.setObjectName("Hint")
        foot.addWidget(self.footer)
        foot.addStretch(1)
        self.log_link = QPushButton("日志")
        self.log_link.setObjectName("Ghost")
        self.log_link.clicked.connect(self._show_log_window)
        foot.addWidget(self.log_link)
        outer.addLayout(foot)

        self.log_view = LogView()
        self.log_view.hide()

    def _page_factories(self) -> list:
        """页面清单。迁移期间缺哪页就退回占位页，保证窗口始终能开。"""
        from . import pages

        return pages.factories()

    # ------------------------------------------------------------ 玻璃与主题

    def _apply_glass(self) -> None:
        """应用窗口模糊。

        用 ``blur.py`` 的序列（抄自 pywinstyles 里**用户实测生效**的实现：
        属性 30 + 属性 19 连续两次调用）。``glass.py`` 那套 DWM 材质属性
        在本机实测拿不到客户区（只有系统画的标题栏会模糊），所以不再使用。
        """
        hwnd = int(self.winId())
        style = self.glass_style
        ok = blur.apply_style(hwnd, style)
        names = {"acrylic": "亚克力模糊", "blur": "老式模糊", "none": "无模糊"}
        label = names.get(style, style)
        if style == "none":
            self.glass_badge.set_state("idle", label)
            self.glass_badge.setToolTip("已关闭窗口模糊（仍为半透明），可在「高级」页切换")
        elif ok:
            self.glass_badge.set_state("ok", label)
            self.glass_badge.setToolTip(
                f"{label} — AccentState={'4' if style == 'acrylic' else '3'}\n"
                "可在「高级」页切换模糊模式"
            )
        else:
            self.glass_badge.set_state("warn", "模糊不可用")
            self.glass_badge.setToolTip("本系统不支持窗口模糊，界面已自动降级为半透明")

    def set_glass_style(self, style: str) -> bool:
        """切换模糊模式（「高级」页调用）。返回是否成功。"""
        self.glass_style = style if style in blur.STYLES else blur.DEFAULT_STYLE
        self._apply_glass()
        try:
            ui = getattr(self.controller.config, "ui", None)
            if ui is not None and hasattr(ui, "glass_style"):
                ui.glass_style = self.glass_style
        except Exception:  # noqa: BLE001
            pass
        return True

    def apply_theme(self, key: str) -> None:
        self.theme_key = key if key in themes.THEMES else themes.DEFAULT_THEME
        palette = themes.get(self.theme_key)

        # 自绘路线要做圆角底；原生材质时让窗口背景全透明，交给系统画
        # 只有自绘路线才给 Root 画圆角半透明底；原生路线把背景交给系统材质
        self.setStyleSheet(
            themes.build_qss(palette, frameless=self.frameless)
        )
        self.btn_theme.setText(f"主题：{palette.name}")

        for page in getattr(self, "pages", []):
            try:
                page.on_theme_changed(palette)
            except Exception:  # noqa: BLE001
                log.debug("页面主题回调失败", exc_info=True)

        # 原生材质要跟着明暗走
        try:
            glass.apply(int(self.winId()), self.capability, dark=palette.is_dark)
        except Exception:  # noqa: BLE001
            pass

        # 持久化（配置里没有 ui 段就跳过，不强行改结构）
        try:
            ui = getattr(self.controller.config, "ui", None)
            if ui is not None and hasattr(ui, "theme"):
                ui.theme = self.theme_key
        except Exception:  # noqa: BLE001
            pass

    def toggle_theme(self) -> None:
        self.apply_theme(themes.flipped(self.theme_key))
        self.toast.show(f"已切换到{themes.get(self.theme_key).name}主题", "info")

    def _toggle_max(self) -> None:
        """保留入口（无边框模式的历史用法）；系统边框模式下由系统处理。"""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ------------------------------------------------------------ 事件

    def _on_event(self, event) -> None:
        # 日志
        if getattr(event, "message", "") and getattr(event, "kind", "") not in (
            "progress", "stats"
        ):
            level = getattr(event, "level", "info")
            if getattr(event, "kind", "") == "file":
                level = "success"
            self.log_view.append_line(event.message, level,
                                      timestamp=_now())
        # 顶部状态
        if getattr(event, "kind", "") == "state":
            state = event.data.get("state")
            mapping = {
                "running": ("ok", "运行中"),
                "paused": ("warn", "已暂停"),
                "stopped": ("idle", "已停止"),
            }
            level, text = mapping.get(state, ("info", str(state)))
            self.state_badge.set_state(level, text)
        # 转发给页面
        for page in self.pages:
            try:
                page.on_event(event)
            except Exception:  # noqa: BLE001
                log.debug("页面事件处理失败", exc_info=True)

    def _show_log_window(self) -> None:
        from PySide6.QtWidgets import QDialog, QVBoxLayout as VBox

        dlg = QDialog(self)
        dlg.setWindowTitle("运行日志")
        dlg.resize(880, 460)
        lay = VBox(dlg)
        lay.setContentsMargins(10, 10, 10, 10)
        view = LogView()
        view.setPlainText("\n".join(self.log_view.tail(2000)))
        lay.addWidget(view)
        dlg.exec()

    # ------------------------------------------------------------ 拖动与缩放

    def _install_drag_and_resize(self) -> None:
        """无边框窗口要自己实现拖动与边缘缩放。"""
        self._drag_offset = None
        self._resize_edge = None
        self._resize_start = None
        self.setMouseTracking(True)
        # ⚠️ 事件过滤器只装在自己身上，**不要**装到 QApplication 上。
        #    踩过的坑：装到 app 上后它会拦截所有控件的鼠标事件，
        #    用户反馈"点一下空白窗口就隐藏了" —— 这类全局过滤器是典型来源。
        #    原生材质模式（系统边框）下系统自己处理缩放，压根不需要它。
        self.installEventFilter(self)

    def _edge_at(self, pos) -> str:
        m = _RESIZE_MARGIN
        x, y = pos.x(), pos.y()
        w, h = self.width(), self.height()
        edges = []
        if y <= m:
            edges.append("t")
        elif y >= h - m:
            edges.append("b")
        if x <= m:
            edges.append("l")
        elif x >= w - m:
            edges.append("r")
        return "".join(edges)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is not self:
            return False                      # 不干预其它控件的事件
        if event.type() == QEvent.Type.MouseMove and not self.isMaximized():
            pos = self.mapFromGlobal(QCursor.pos())
            if self.rect().contains(pos):
                edge = self._edge_at(pos)
                cursors = {
                    "t": Qt.CursorShape.SizeVerCursor,
                    "b": Qt.CursorShape.SizeVerCursor,
                    "l": Qt.CursorShape.SizeHorCursor,
                    "r": Qt.CursorShape.SizeHorCursor,
                    "tl": Qt.CursorShape.SizeFDiagCursor,
                    "br": Qt.CursorShape.SizeFDiagCursor,
                    "tr": Qt.CursorShape.SizeBDiagCursor,
                    "bl": Qt.CursorShape.SizeBDiagCursor,
                }
                self.setCursor(cursors.get(edge, Qt.CursorShape.ArrowCursor))
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            edge = self._edge_at(pos)
            if edge and not self.isMaximized():
                self._resize_edge = edge
                self._resize_start = (event.globalPosition().toPoint(),
                                      self.geometry())
            else:
                self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._resize_edge and self._resize_start:
            start_pt, start_geo = self._resize_start
            delta = event.globalPosition().toPoint() - start_pt
            geo = self.geometry()
            if "l" in self._resize_edge:
                geo.setLeft(min(start_geo.left() + delta.x(), start_geo.right() - 400))
            if "r" in self._resize_edge:
                geo.setRight(max(start_geo.right() + delta.x(), start_geo.left() + 400))
            if "t" in self._resize_edge:
                geo.setTop(min(start_geo.top() + delta.y(), start_geo.bottom() - 300))
            if "b" in self._resize_edge:
                geo.setBottom(max(start_geo.bottom() + delta.y(), start_geo.top() + 300))
            self.setGeometry(geo)
        elif self._drag_offset is not None and not self.isMaximized():
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None
        self._resize_edge = None
        self._resize_start = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        self.bridge.start()
        for page in self.pages:
            for hook in ("on_start",):
                fn = getattr(page, hook, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        log.debug("页面启动钩子失败", exc_info=True)

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.bridge.stop()
        except Exception:  # noqa: BLE001
            pass
        super().closeEvent(event)


def _now() -> str:
    import time

    return time.strftime("%H:%M:%S")
