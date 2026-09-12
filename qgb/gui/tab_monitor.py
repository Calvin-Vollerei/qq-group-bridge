"""监控页：启停控制、实时状态、统计、日志。"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..controller import Event, state_counts_summary
from ..models import TransferState
from .base import Tab
from .theme import Palette, ui_font
from .widgets import Card, Hint, LogView, StatusPill

__all__ = ["MonitorTab"]

#: 状态 -> 胶囊显示
_STATE_PILL = {
    "running": ("running", "运行中"),
    "paused": ("paused", "已暂停"),
    "stopped": ("stopped", "已停止"),
}


class _StatBox(ttk.Frame):
    """一个统计小方块。"""

    def __init__(self, master, label: str, *, scale: float = 1.0) -> None:
        super().__init__(master, style="Card.TFrame")
        self.value = ttk.Label(self, text="0", style="Card.TLabel",
                               font=ui_font(scale, size=17, bold=True))
        self.value.pack(anchor="w")
        ttk.Label(self, text=label, style="CardMuted.TLabel").pack(anchor="w")

    def set(self, value: str) -> None:
        self.value.configure(text=value)


class MonitorTab(Tab):
    title = "监控"

    # -------------------------------------------------- 构建

    def build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # ---------- 顶部：状态 + 控制
        top = Card(self, scale=self.scale)
        top.grid(row=0, column=0, sticky="ew")

        line = ttk.Frame(top, style="Card.TFrame")
        line.pack(fill="x")

        self.pill = StatusPill(line, scale=self.scale)
        self.pill.pack(side="left")

        self.state_detail = ttk.Label(line, text="未启动", style="CardMuted.TLabel")
        self.state_detail.pack(side="left", padx=(12, 0))

        self.btn_start = ttk.Button(line, text="▶  开始监控", style="Accent.TButton",
                                    command=self._start)
        self.btn_start.pack(side="right")
        self.btn_stop = ttk.Button(line, text="■  停止", style="Ghost.TButton",
                                   command=self._stop)
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_pause = ttk.Button(line, text="⏸  暂停", style="Ghost.TButton",
                                    command=self._toggle_pause)
        self.btn_pause.pack(side="right", padx=(0, 8))

        mini = ttk.Frame(top, style="Card.TFrame")
        mini.pack(fill="x", pady=(10, 0))
        # 这个按钮就是「刷新」：拉取群里的新文件并搬运。
        # 常驻监控运行时也能点（流水线有可重入锁），不必干等一个轮询周期。
        self.btn_once = ttk.Button(mini, text="🔄  立即刷新（拉取新文件）",
                                   style="Accent.TButton", command=self._run_once)
        self.btn_once.pack(side="left")
        ttk.Button(mini, text="重试失败项", style="Ghost.TButton",
                   command=self._requeue).pack(side="left", padx=(8, 0))
        ttk.Button(mini, text="查看搬运记录", style="Ghost.TButton",
                   command=self._show_records).pack(side="left", padx=(8, 0))

        # ---------- 进度
        prog = Card(self, scale=self.scale)
        prog.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.progress_text = ttk.Label(prog, text="空闲", style="Card.TLabel")
        self.progress_text.pack(anchor="w")
        self.progress = ttk.Progressbar(prog, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(6, 0))

        # ---------- 统计
        stats = Card(self, scale=self.scale)
        stats.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        row = ttk.Frame(stats, style="Card.TFrame")
        row.pack(fill="x")
        self.boxes: dict[str, _StatBox] = {}
        for key, label in (
            ("seen", "已见文件"),
            ("pending", "待处理"),
            ("done", "已完成"),
            ("failed", "失败"),
            ("traffic", "已上传"),
        ):
            box = _StatBox(row, label, scale=self.scale)
            box.pack(side="left", expand=True, fill="x")
            self.boxes[key] = box

        self.counts_label = ttk.Label(stats, text="", style="CardMuted.TLabel")
        self.counts_label.pack(anchor="w", pady=(10, 0))

        # ---------- 日志
        log_card = Card(self, scale=self.scale)
        log_card.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        log_card.rowconfigure(1, weight=1)
        log_card.columnconfigure(0, weight=1)

        header = ttk.Frame(log_card, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(header, text="运行日志", style="CardHead.TLabel").pack(side="left")
        self.log_view = LogView(log_card, scale=self.scale)
        # ⚠️ controls() 返回的是新建的控件条，必须显式放置，否则它会以 1x1
        # 尺寸存在但不可见（检查器把这类问题报为「未放置」）。
        bar = self.log_view.controls(header)
        bar.pack(side="right")

        self.log_view.grid(row=1, column=0, sticky="nsew")

        Hint(
            log_card,
            "日志已自动脱敏：令牌、Cookie、手机号等敏感信息不会写入磁盘。",
            scale=self.scale,
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        self._last_stats = 0.0
        self.refresh()

    # -------------------------------------------------- 刷新

    def refresh(self) -> None:
        state = self.controller.monitor_state()
        pill_state, text = _STATE_PILL.get(state, ("stopped", "已停止"))
        if state == "paused":
            # 区分「人工暂停」和「等登录」
            self.pill.set_state("waiting" if self._awaiting_login else "paused",
                                "等待登录" if self._awaiting_login else text)
        else:
            self.pill.set_state(pill_state, text)

        self.state_detail.configure(text=self._state_detail(state))
        self._update_buttons(state)
        self._refresh_stats()

    _awaiting_login = False

    def _state_detail(self, state: str) -> str:
        cfg = self.controller.config
        if state == "running":
            return f"正在监控 {len(cfg.groups)} 个群 · 每 {cfg.monitor.poll_interval_sec} 秒轮询一次"
        if state == "paused":
            return "已暂停（QQ 未登录时也会自动暂停）"
        return "未启动"

    def _update_buttons(self, state: str) -> None:
        running = state in ("running", "paused")
        self.btn_start.state(["disabled"] if running else ["!disabled"])
        self.btn_stop.state(["!disabled"] if running else ["disabled"])
        self.btn_pause.state(["!disabled"] if running else ["disabled"])
        self.btn_pause.configure(text="▶  恢复" if state == "paused" else "⏸  暂停")

    def _refresh_stats(self) -> None:
        data = self.controller.stats()
        counts = data.get("counts", {}) or {}

        def c(name: str) -> int:
            return int(counts.get(name, 0) or 0)

        pending = c(TransferState.DISCOVERED.value) + c(TransferState.DOWNLOADING.value) \
            + c(TransferState.DOWNLOADED.value) + c(TransferState.UPLOADING.value)
        done = c(TransferState.DONE.value) + c(TransferState.UPLOADED.value)

        self.boxes["seen"].set(str(data.get("seen", 0)))
        self.boxes["pending"].set(str(pending))
        self.boxes["done"].set(str(done))
        self.boxes["failed"].set(str(c(TransferState.FAILED.value)))
        self.boxes["traffic"].set(str(data.get("bytes_text", "0 B")))
        self.counts_label.configure(text=state_counts_summary(counts))

    # -------------------------------------------------- 动作

    def _start(self) -> None:
        if self.app is not None:
            self.app.save_all()          # 先落盘，避免用旧配置启动
        if self.controller.start_monitor():
            self.toast("监控已启动", "success")
        self.refresh()

    def _stop(self) -> None:
        self._awaiting_login = False
        self.controller.stop_monitor()
        self.toast("监控已停止", "muted")
        self.refresh()

    def _toggle_pause(self) -> None:
        if self.controller.monitor_state() == "paused":
            self._awaiting_login = False
            self.controller.resume_monitor()
            self.toast("已恢复监控", "success")
        else:
            self.controller.pause_monitor()
            self.toast("已暂停监控", "muted")
        self.refresh()

    def _run_once(self) -> None:
        if self.app is not None:
            self.app.save_all()
        if self.controller.run_once_now():
            self.toast("正在执行一轮搬运…", "info")

    def _requeue(self) -> None:
        n = self.controller.requeue_failed()
        self.toast(f"已把 {n} 个失败项重新排队", "success" if n else "muted")
        self._refresh_stats()

    def _show_records(self) -> None:
        rows = self.controller.recent_records(limit=300)
        win = tk.Toplevel(self)
        win.title("搬运记录")
        win.geometry("900x460")
        win.configure(bg=Palette.BG)
        win.transient(self.winfo_toplevel())

        ttk.Label(win, text="最近处理的文件", style="Head.TLabel",
                  padding=(12, 10)).pack(anchor="w")

        wrap = ttk.Frame(win, padding=(12, 0, 12, 12))
        wrap.pack(fill="both", expand=True)

        columns = ("name", "group", "size", "state", "updated")
        tree = ttk.Treeview(wrap, columns=columns, show="headings")
        for col, text, width in (
            ("name", "文件名", 320),
            ("group", "群号", 110),
            ("size", "大小", 90),
            ("state", "状态", 90),
            ("updated", "更新时间", 150),
        ):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="w")

        from ..utils import human_size

        label_map = {
            TransferState.DONE.value: "已完成",
            TransferState.UPLOADED.value: "已上传",
            TransferState.FAILED.value: "失败",
            TransferState.FILTERED_OUT.value: "已过滤",
            TransferState.DISCOVERED.value: "待处理",
            TransferState.DOWNLOADING.value: "下载中",
            TransferState.DOWNLOADED.value: "已下载",
            TransferState.UPLOADING.value: "上传中",
            TransferState.SKIPPED.value: "已跳过",
            TransferState.EXPIRED.value: "已过期",
        }
        for row in rows:
            tree.insert("", "end", values=(
                row.get("name", ""),
                _mask_group(row.get("group_id", "")),
                human_size(row.get("size", 0)),
                label_map.get(row.get("state", ""), row.get("state", "")),
                time.strftime("%Y-%m-%d %H:%M", time.localtime(row.get("updated_at") or 0)),
            ))

        scroll = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        if not rows:
            ttk.Label(win, text="暂无记录", style="Muted.TLabel").pack(pady=6)

    # -------------------------------------------------- 事件

    def handle_event(self, event: Event) -> None:
        # 日志
        if event.message and event.kind not in ("progress", "stats"):
            level = event.level
            if event.kind == "file":
                level = "success"
            self.log_view.append(
                event.message, level, timestamp=time.strftime("%H:%M:%S")
            )

        if event.kind == "login":
            category = event.data.get("category")
            self._awaiting_login = category in ("offline", "unknown")
            self.refresh()

        if event.kind == "state":
            state = event.data.get("state")
            if state == "running":
                self._awaiting_login = False
            self.refresh()

        if event.kind == "progress":
            data = event.data
            name = str(data.get("name", ""))
            percent = float(data.get("percent") or 0.0)
            phase = "上传" if data.get("phase") == "upload" else "下载"
            speed = data.get("speed")
            text = f"{phase}中：{name}　{percent:.0f}%"
            if speed:
                from ..utils import human_size

                text += f"　{human_size(speed)}/s"
            self.progress_text.configure(text=text)
            self.progress.configure(value=percent)

        if event.kind == "stats":
            self.progress_text.configure(text="空闲")
            self.progress.configure(value=0)
            self._refresh_stats()

        if event.kind == "error" and event.level == "error":
            self.refresh()

        if event.kind.startswith("task:") and event.kind.endswith("run_once"):
            self.refresh()


def _mask_group(group: str) -> str:
    """记录列表里群号做遮蔽（部署方界面无需回显完整群号）。"""
    from ..redact import mask_id

    return mask_id(group)
