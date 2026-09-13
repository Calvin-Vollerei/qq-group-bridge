"""页面清单与实现。

迁移策略：**先让外壳能开、能切换主题**，页面一页一页搬。
没搬完的页面用 ``PlaceholderPage`` 顶上 —— 保证任何时刻这个窗口都能正常打开。

已实现：

* ``MonitorPage`` 监控页（指标 + 控制 + 进度 + 日志），真实接入控制器

待实现（占位）：

* 群与规则 / QQ 登录 / 网盘与凭据 / 高级
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .shell import Page
from .widgets import Card, Hint, LogView, Metric, SectionTitle, StatusBadge

log = logging.getLogger(__name__)


class PlaceholderPage(Page):
    """尚未迁移的页面：明确告诉用户"这页还在迁移"，而不是给个空白。"""

    title = "（待迁移）"

    def __init__(self, shell, title: str, note: str = "") -> None:
        super().__init__(shell)
        self.title = title
        self._note = note

    def build(self) -> QWidget:
        card = Card()
        card.add(SectionTitle(self.title))
        card.add(Hint(
            "这个页面还在迁移到新界面（PySide6 + 毛玻璃）的过程中。\n"
            "搬运功能本身不受影响 —— 监控页已经可用；"
            "需要完整界面时可以先用旧版界面启动（run_bridge.py --ui tk）。"
        ))
        if self._note:
            card.add(Hint(self._note))

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.addWidget(card)
        lay.addStretch(1)
        return root


class MonitorPage(Page):
    """监控页：状态、指标、控制按钮、进度、日志。"""

    title = "监控"

    def _build(self) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        # 滚动区与它的 viewport 默认会画一层底，会把窗口背后的 DWM 材质挡住
        # （用户实测：只有标题栏那条模糊，主内容区黑的）。
        area.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget, QScrollArea > QWidget "
            "{ background: transparent; border: none; }"
        )
        area.viewport().setAutoFillBackground(False)
        area.setAutoFillBackground(False)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # ---- 控制卡
        control = Card()
        head = control.row()
        head.addWidget(SectionTitle("搬运控制"))
        head.addStretch(1)
        self.state_badge = StatusBadge("未启动")
        head.addWidget(self.state_badge)

        btns = control.row()
        self.btn_start = QPushButton("▶  开始监控")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self._start)
        btns.addWidget(self.btn_start)

        self.btn_pause = QPushButton("⏸  暂停")
        self.btn_pause.clicked.connect(self._toggle_pause)
        btns.addWidget(self.btn_pause)

        self.btn_stop = QPushButton("■  停止")
        self.btn_stop.clicked.connect(self._stop)
        btns.addWidget(self.btn_stop)

        self.btn_once = QPushButton("🔄  立即刷新（拉取新文件）")
        self.btn_once.clicked.connect(self._run_once)
        btns.addWidget(self.btn_once)

        self.btn_requeue = QPushButton("重试失败项")
        self.btn_requeue.setObjectName("Ghost")
        self.btn_requeue.clicked.connect(self._requeue)
        btns.addWidget(self.btn_requeue)
        btns.addStretch(1)
        lay.addWidget(control)

        # ---- 指标卡
        metrics = Card()
        metrics.add(SectionTitle("统计"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)
        self.metrics: dict[str, Metric] = {}
        specs = [
            ("seen", "已见文件"), ("new_cycle", "本轮新发现"),
            ("pending", "待处理"), ("done", "已搬完"),
            ("failed", "失败"), ("filtered", "已过滤"),
        ]
        for i, (key, label) in enumerate(specs):
            m = Metric(label, "0")
            self.metrics[key] = m
            grid.addWidget(m, i // 3, i % 3)
        metrics.body.addLayout(grid)
        self.summary = Hint("")
        metrics.add(self.summary)
        lay.addWidget(metrics)

        # ---- 进度 + 日志
        prog = Card()
        prog.add(SectionTitle("当前进度"))
        self.progress_label = Hint("空闲")
        prog.add(self.progress_label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        prog.add(self.bar)
        lay.addWidget(prog)

        logcard = Card()
        logcard.add(SectionTitle("运行日志"))
        self.log = LogView()
        self.log.setMinimumHeight(200)
        logcard.add(self.log, 1)
        lay.addWidget(logcard, 1)

        lay.addStretch(0)
        area.setWidget(root)
        return area

    # 兼容基类协议：build() 里调用 _build()
    def build(self) -> QWidget:      # noqa: D102
        return self._build()

    # ------------------------------------------------------------ 动作

    def _start(self) -> None:
        try:
            ok = self.controller.start_monitor()
            self.toast.show("已开始监控" if ok else "监控已在运行", "success" if ok else "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"启动失败：{type(exc).__name__}", "error")

    def _stop(self) -> None:
        try:
            self.controller.stop_monitor()
            self.toast.show("已停止监控", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"停止失败：{type(exc).__name__}", "error")

    def _toggle_pause(self) -> None:
        try:
            state = self.controller.monitor_state()
            if state == "paused":
                self.controller.resume_monitor()
                self.toast.show("已恢复", "success")
            else:
                self.controller.pause_monitor()
                self.toast.show("已暂停", "warning")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"操作失败：{type(exc).__name__}", "error")

    def _run_once(self) -> None:
        try:
            started = self.controller.run_once_now()
            self.toast.show(
                "已开始一轮扫描" if started else "当前一轮还没结束，已跳过",
                "success" if started else "warning",
            )
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"刷新失败：{type(exc).__name__}", "error")

    def _requeue(self) -> None:
        try:
            n = self.controller.requeue_failed()
            self.toast.show(f"已把 {n} 个失败项重新排队", "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"操作失败：{type(exc).__name__}", "error")

    # ------------------------------------------------------------ 事件

    def on_start(self) -> None:
        self._refresh_stats()

    def on_event(self, event) -> None:
        kind = getattr(event, "kind", "")
        if kind == "state":
            state = event.data.get("state")
            mapping = {
                "running": ("ok", "运行中"),
                "paused": ("warn", "已暂停"),
                "stopped": ("idle", "已停止"),
            }
            level, text = mapping.get(state, ("info", str(state)))
            self.state_badge.set_state(level, text)
            self.btn_pause.setText("▶  恢复" if state == "paused" else "⏸  暂停")
        elif kind == "stats":
            self._update_stats(event.data)
        elif kind == "progress":
            data = event.data or {}
            name = data.get("name", "")
            pct = float(data.get("percent") or 0)
            self.bar.setValue(int(max(0.0, min(100.0, pct))))
            speed = data.get("speed_text") or ""
            eta = data.get("eta_text") or ""
            self.progress_label.setText(
                f"{name}　{pct:.1f}%　{speed}　{eta}".strip()
            )
        elif kind == "file":
            self.bar.setValue(100)
            self.progress_label.setText(event.message or "完成")
            self._refresh_stats()

    # ------------------------------------------------------------ 统计

    def _refresh_stats(self) -> None:
        try:
            self._update_stats(self.controller.stats())
        except Exception:  # noqa: BLE001
            log.debug("取统计失败", exc_info=True)

    def _update_stats(self, data: dict) -> None:
        counts = (data or {}).get("counts") or {}
        self.metrics["seen"].set((data or {}).get("seen", 0))
        self.metrics["new_cycle"].set((data or {}).get("new_last_cycle", 0))
        self.metrics["pending"].set(counts.get("discovered", 0))
        self.metrics["done"].set(
            int(counts.get("done", 0)) + int(counts.get("uploaded", 0))
        )
        self.metrics["failed"].set(counts.get("failed", 0))
        self.metrics["filtered"].set(counts.get("filtered_out", 0))
        uptime = float((data or {}).get("uptime") or 0)
        bytes_text = (data or {}).get("bytes_text") or "0 B"
        hours, rem = divmod(int(uptime), 3600)
        minutes = rem // 60
        self.summary.setText(f"已上传 {bytes_text}　运行时长 {hours} 小时 {minutes} 分")


def _advanced(shell):
    """懒加载「高级」页（含模糊模式切换）。"""
    from .page_advanced import AdvancedPage

    return AdvancedPage(shell)


def factories() -> list:
    """返回按顺序创建页面的可调用对象列表。

    未迁移完的页面用占位页，保证窗口任何时刻都能打开。
    """
    return [
        MonitorPage,
        lambda shell: PlaceholderPage(shell, "群与规则",
                                      "将支持：群号增删、关键字包含/排除、体积与后缀过滤。"),
        lambda shell: PlaceholderPage(shell, "QQ 登录",
                                      "将支持：NapCat 托管、二维码登录、组件安装与状态。"),
        lambda shell: PlaceholderPage(shell, "网盘与凭据",
                                      "将支持：WebDAV 地址与凭据、连接测试、远端目录。"),
        lambda shell: _advanced(shell),
    ]
