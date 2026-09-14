"""高级页：外观信息（只读）+ 维护操作。

按用户要求**取消**了主题切换与模糊模式选择：
* 主题固定为单套深色（对比度调优）；
* 模糊固定为 **aero（BLURBEHIND）**，且性能优先。

这里只**展示**当前外观状态（便于排障），不再提供切换控件。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import blur
from .shell import Page
from .widgets import Card, Hint, SectionTitle, StatusBadge

log = logging.getLogger(__name__)


class AdvancedPage(Page):
    """高级：外观状态 + 维护操作。"""

    title = "高级"

    def build(self) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget, QScrollArea > QWidget "
            "{ background: transparent; border: none; }"
        )
        area.viewport().setAutoFillBackground(False)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # ---------------- 外观（只读）
        look = Card()
        head = look.row()
        head.addWidget(SectionTitle("窗口外观"))
        head.addStretch(1)
        self.glass_badge = StatusBadge("—")
        head.addWidget(self.glass_badge)

        self.look_info = Hint("")
        look.add(self.look_info)
        look.add(Hint(
            "外观按「性能优先」固定：\n"
            "· 模糊 —— aero（AccentState=3，系统原生，开销低）\n"
            "· 主题 —— 单套深色（按对比度调优，无切换开销）\n"
            "换肤/切换会重建整套样式表，对毛玻璃窗口有明显卡顿，因此已取消。"
        ))
        lay.addWidget(look)

        # ---------------- 维护
        maint = Card()
        maint.add(SectionTitle("维护"))
        row = maint.row()
        for text, slot, style in (
            ("重试失败项", self._requeue, "primary"),
            ("清空记录并重新发现", self._reset, "ghost"),
            ("立即刷新（拉取新文件）", self._run_once, "ghost"),
        ):
            btn = QPushButton(text)
            if style == "primary":
                btn.setObjectName("Primary")
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        maint.add(Hint(
            "「清空记录并重新发现」会清掉累计的待处理/已过滤/失败记录"
            "（保留已搬完的去重记录），用于队列越积越多的情况。"
        ))
        lay.addWidget(maint)

        # ---------------- 关于
        about = Card()
        about.add(SectionTitle("关于"))
        try:
            from ..version import APP_NAME, __version__

            about.add(Hint(f"{APP_NAME}　v{__version__}"))
        except Exception:  # noqa: BLE001
            about.add(Hint("QQ群文件搬运工"))
        about.add(Hint(
            "界面：PySide6（Qt 6）+ 原生窗口模糊（aero）\n"
            "旧版 Tk 界面仍可用：run_bridge.py --ui tk"
        ))
        lay.addWidget(about)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    def on_start(self) -> None:
        shell = self.shell
        style = getattr(shell, "glass_style", blur.DEFAULT_STYLE)
        label = blur.STYLES.get(style, ("模糊", None))[0]
        self.glass_badge.set_state("ok" if style != "none" else "idle", label)
        self.look_info.setText(
            f"模糊模式：{label}　|　主题：单套深色　|　渲染：Qt 6 (PySide6)"
        )

    # -------------------------------------------------- 动作

    def _requeue(self) -> None:
        try:
            n = self.controller.requeue_failed()
            self.toast.show(f"已把 {n} 个失败项重新排队", "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"操作失败：{type(exc).__name__}", "error")

    def _reset(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(
            self, "清空记录并重新发现",
            "将清空「待处理 / 已过滤 / 失败」的累计记录，然后重新扫描群文件。\n\n"
            "· 已搬完的记录会保留（用于去重，不会重复上传）\n"
            "· 下次扫描会按当前群文件重新建立待办列表\n\n确定继续吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.controller.reset_discovery()
            self.toast.show(
                f"已清空 {result.get('removed', 0)} 条累计记录，点「立即刷新」重新发现",
                "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"清空失败：{type(exc).__name__}", "error")

    def _run_once(self) -> None:
        try:
            started = self.controller.run_once_now()
            self.toast.show("已开始一轮扫描" if started else "当前一轮还没结束，已跳过",
                            "success" if started else "warning")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"刷新失败：{type(exc).__name__}", "error")
