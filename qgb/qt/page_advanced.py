"""高级页：窗口模糊模式、主题、以及搬运/清理等维护操作。

用户要求：**高级里加一个切模式** —— 即窗口模糊模式的选择
（亚克力 / 老式模糊 / 无模糊），切换后立即生效并写回配置。
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import blur, themes
from .shell import Page
from .widgets import Card, Hint, SectionTitle, StatusBadge

log = logging.getLogger(__name__)


class AdvancedPage(Page):
    """高级：外观（模糊模式 / 主题）+ 维护操作。"""

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

        # ---------------- 外观：模糊模式
        look = Card()
        head = look.row()
        head.addWidget(SectionTitle("窗口外观"))
        head.addStretch(1)
        self.glass_badge = StatusBadge("—")
        head.addWidget(self.glass_badge)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)

        grid.addWidget(QLabel("模糊模式"), 0, 0)
        self.glass_box = QComboBox()
        for key, (label, _fn) in blur.STYLES.items():
            self.glass_box.addItem(label, key)
        self.glass_box.currentIndexChanged.connect(self._on_glass_changed)
        grid.addWidget(self.glass_box, 0, 1)

        grid.addWidget(QLabel("主题"), 1, 0)
        self.theme_box = QComboBox()
        for key, palette in themes.THEMES.items():
            self.theme_box.addItem(palette.name, key)
        self.theme_box.currentIndexChanged.connect(self._on_theme_changed)
        grid.addWidget(self.theme_box, 1, 1)
        look.body.addLayout(grid)

        look.add(Hint(
            "模糊模式说明：\n"
            "· 亚克力模糊 —— AccentState=4，清透，推荐\n"
            "· 老式模糊 —— AccentState=3，偏灰一些\n"
            "· 无模糊 —— 只保留半透明（旧系统或不想吃 GPU 时选它）\n"
            "切换后立即生效；若系统不支持模糊，会自动降级为半透明。"
        ))
        lay.addWidget(look)

        # ---------------- 维护操作
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
            "界面：PySide6（Qt 6）+ 原生窗口模糊\n"
            "旧版 Tk 界面仍可用：run_bridge.py --ui tk"
        ))
        lay.addWidget(about)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    def on_start(self) -> None:
        self._sync_from_config()

    def _sync_from_config(self) -> None:
        """把当前实际状态回填到控件（避免显示与实际不一致）。"""
        shell = self.shell
        try:
            style = getattr(shell, "glass_style", blur.DEFAULT_STYLE)
            idx = self.glass_box.findData(style)
            if idx >= 0:
                self.glass_box.blockSignals(True)
                self.glass_box.setCurrentIndex(idx)
                self.glass_box.blockSignals(False)
            self.glass_badge.set_state(
                "ok" if style != "none" else "idle",
                dict(((k, v[0]) for k, v in blur.STYLES.items())).get(style, style),
            )
            tidx = self.theme_box.findData(getattr(shell, "theme_key", "dark"))
            if tidx >= 0:
                self.theme_box.blockSignals(True)
                self.theme_box.setCurrentIndex(tidx)
                self.theme_box.blockSignals(False)
        except Exception:  # noqa: BLE001
            log.debug("回填外观设置失败", exc_info=True)

    # -------------------------------------------------- 动作

    def _on_glass_changed(self, _index: int) -> None:
        key = self.glass_box.currentData()
        ok = self.shell.set_glass_style(key)
        label = dict(((k, v[0]) for k, v in blur.STYLES.items())).get(key, key)
        self.glass_badge.set_state("ok" if ok and key != "none" else "idle", label)
        self.toast.show(f"模糊模式已切换为「{label}」", "success" if ok else "warning")

    def _on_theme_changed(self, _index: int) -> None:
        key = self.theme_box.currentData()
        self.shell.apply_theme(key)
        self.toast.show(f"主题已切换为「{themes.get(key).name}」", "info")

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
