"""可复用控件：卡片、区块标题、指标框、日志视图、状态条。

这些是五个页面共用的"毛玻璃 UI 基本块"，集中在这里以免每个页面各写一套样式
（旧 Tk 版就有这个问题：``widgets.py`` 之外还有人自己拼 Frame）。
"""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class Card(QFrame):
    """毛玻璃卡片容器。``strong=True`` 时用更实的底色（弹窗/列表用）。"""

    def __init__(self, parent: QWidget | None = None, *, strong: bool = False,
                 padding: tuple[int, int, int, int] = (16, 14, 16, 14)) -> None:
        super().__init__(parent)
        self.setObjectName("CardStrong" if strong else "Card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*padding)
        lay.setSpacing(10)
        self.body = lay

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def row(self, spacing: int = 8) -> QHBoxLayout:
        """返回卡片的**横向行**（同一张卡复用同一行）。

        踩过的坑：早期每次调用都新建一个 QHBoxLayout 并 addLayout 到卡片上，
        而卡片往往会被调用两次（一次放标题、一次放按钮）——
        嵌套布局换来换去会触发 Qt 的
        ``QLayout::addChildLayout: layout ... already has a parent`` 警告。
        复用同一行即可：卡片本来就只需要一行。
        """
        box = getattr(self, "_row", None)
        if box is None:
            # ⚠️ 这里**不能**写 QHBoxLayout(self) —— 卡片已经有一个布局
            #    （self.body），再给同一个控件装第二个布局，Qt 会报
            #    "Attempting to add QLayout to Card, which already has a layout"。
            #    正确做法：无父创建，由 addLayout 把它挂到 body 下。
            #    （两处警告都是用 qInstallMessageHandler 打调用栈定位到的。）
            box = QHBoxLayout()
            box.setSpacing(spacing)
            self.body.addLayout(box)
            self._row = box
        return box


class SectionTitle(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Subtitle")


class Hint(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Hint")
        self.setWordWrap(True)


class Separator(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Sep")
        self.setFixedHeight(1)


class Metric(QWidget):
    """一个指标框：标题 + 数值 +（可选）单位/副标题。"""

    def __init__(self, label: str, value: str = "0",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.caption = QLabel(label)
        self.caption.setObjectName("Hint")
        lay.addWidget(self.caption)

        self.value = QLabel(value)
        self.value.setObjectName("Metric")
        lay.addWidget(self.value)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set(self, value: object) -> None:
        self.value.setText(str(value))


class StatusBadge(QLabel):
    """小徽标：运行中 / 已安装 / 未安装 之类。"""

    LEVELS = {
        "ok": "#4ED17E",
        "warn": "#F2B440",
        "error": "#FF6B6B",
        "idle": "#8A93A1",
        "info": "#5B9BFF",
    }

    def __init__(self, text: str = "未知", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set_state("idle", text)

    def set_state(self, level: str, text: str | None = None) -> None:
        color = self.LEVELS.get(level, self.LEVELS["idle"])
        if text is not None:
            self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ background: {color}; color: #10141C; border-radius: 9px;"
            f" padding: 3px 10px; font-size: 12px; font-weight: 600; }}"
        )


class LogView(QPlainTextEdit):
    """日志视图：追加时自动滚到底，并限制最大行数（避免长跑后内存无限增长）。"""

    MAX_BLOCKS = 3000

    LEVEL_COLOR = {
        "info": None,               # 用主题默认色
        "success": "#4ED17E",
        "warning": "#F2B440",
        "error": "#FF6B6B",
        "debug": "#8A93A1",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(self.MAX_BLOCKS)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        # 等宽字体，日志对齐更整齐
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.setFont(font)

    def append_line(self, text: str, level: str = "info",
                    timestamp: str | None = None) -> None:
        color = self.LEVEL_COLOR.get(level)
        prefix = f"{timestamp} " if timestamp else ""
        safe = (prefix + text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if color:
            self.appendHtml(f'<span style="color:{color}">{safe}</span>')
        else:
            self.appendPlainText(prefix + text)
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def tail(self, lines: int = 200) -> list[str]:
        return self.toPlainText().splitlines()[-lines:]


class Toast(QLabel):
    """右下角浮出提示，2.6 秒后自动淡出移除。"""

    def __init__(self, parent: QWidget, text: str, level: str = "info") -> None:
        super().__init__(text, parent)
        colors = {
            "success": ("#4ED17E", "#0B1220"),
            "warning": ("#F2B440", "#1A1408"),
            "error": ("#FF6B6B", "#1A0B0B"),
            "info": ("#5B9BFF", "#0B1220"),
        }
        bg, fg = colors.get(level, colors["info"])
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 10px;"
            f" padding: 9px 14px; font-weight: 600; }}"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        QTimer.singleShot(2600, self._fade)

    def _place(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        margin = 18
        self.move(max(0, parent.width() - self.width() - margin),
                  max(0, parent.height() - self.height() - margin))

    def _fade(self) -> None:
        self.deleteLater()


class ToastHost:
    """给任意 QWidget 挂 toast 能力的小混入（组合优于继承）。"""

    def __init__(self, widget: QWidget) -> None:
        self._widget = widget

    def show(self, text: str, level: str = "info") -> None:
        Toast(self._widget, text, level)


def hline(parent: QWidget | None = None) -> QWidget:
    line = QFrame(parent)
    line.setObjectName("Sep")
    line.setFixedHeight(1)
    return line


def button_row(specs: Iterable[tuple[str, object, str]]) -> tuple[QWidget, dict[str, object]]:
    """快速生成一行按钮。

    ``specs`` 为 ``[(文字, 回调, 样式名)]``，样式名取 ``primary`` / ``ghost`` / ``danger``。
    返回 ``(容器, {文字: 按钮})``。
    """
    from PySide6.QtWidgets import QPushButton

    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    made: dict[str, object] = {}
    for text, callback, style in specs:
        btn = QPushButton(text)
        if style == "primary":
            btn.setObjectName("Primary")
        elif style == "danger":
            btn.setObjectName("Danger")
        elif style == "ghost":
            btn.setObjectName("Ghost")
        if callable(callback):
            btn.clicked.connect(callback)
        lay.addWidget(btn)
        made[text] = btn
    lay.addStretch(1)
    return box, made
