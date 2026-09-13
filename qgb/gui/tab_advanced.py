"""高级设置页：节奏、可靠性、资源保护、路径与自检。"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk

from ..controller import Event, state_counts_summary
from .base import ScrollFrame, Tab
from .theme import Palette
from .widgets import Card, Field, Hint, KVRow, PathPicker

__all__ = ["AdvancedTab"]


class AdvancedTab(Tab):
    title = "高级"

    def build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        scroller = ScrollFrame(self)
        scroller.grid(row=0, column=0, sticky="nsew")
        body = scroller.inner
        body.columnconfigure(0, weight=1)

        # ---------- 节奏
        pace = Card(body, scale=self.scale)
        pace.grid(row=0, column=0, sticky="ew")
        for col in range(4):
            pace.columnconfigure(col, weight=1)

        ttk.Label(pace, text="监控节奏", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 8)
        )

        self.f_poll = Field(pace, "轮询间隔（秒）", scale=self.scale, width=12,
                            hint="建议 ≥ 120，过短会显著提高 QQ 风控概率")
        self.f_poll.grid(row=1, column=0, sticky="ew", padx=(0, 10))

        self.f_jitter = Field(pace, "轮询抖动（秒）", scale=self.scale, width=12,
                              hint="在间隔上叠加随机量，避免固定节律")
        self.f_jitter.grid(row=1, column=1, sticky="ew", padx=(0, 10))

        self.f_conc = Field(pace, "并发下载数", scale=self.scale, width=12,
                            hint="强烈建议保持 1")
        self.f_conc.grid(row=1, column=2, sticky="ew", padx=(0, 10))

        self.f_page = Field(pace, "每页拉取条数", scale=self.scale, width=12,
                            hint="一次向 QQ 要多少条文件记录")
        self.f_page.grid(row=1, column=3, sticky="ew")

        self.var_recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(pace, text="抓取群文件夹（子目录）内的文件",
                        variable=self.var_recursive,
                        style="Card.TCheckbutton").grid(row=2, column=0, columnspan=2,
                                                        sticky="w", pady=(10, 0))

        # ---------- 可靠性
        rel = Card(body, scale=self.scale)
        rel.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for col in range(3):
            rel.columnconfigure(col, weight=1)

        ttk.Label(rel, text="失败处理", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )

        self.f_retries = Field(rel, "单文件最大重试次数", scale=self.scale, width=14)
        self.f_retries.grid(row=1, column=0, sticky="ew", padx=(0, 10))

        self.f_backoff = Field(rel, "重试退避基数（秒）", scale=self.scale, width=14,
                               hint="第 N 次重试等待 N × 基数")
        self.f_backoff.grid(row=1, column=1, sticky="ew", padx=(0, 10))

        self.f_keep = Field(rel, "本地副本保留天数", scale=self.scale, width=14,
                            hint="0 表示上传成功后立即删除，节省磁盘")
        self.f_keep.grid(row=1, column=2, sticky="ew")

        self.f_stall = Field(rel, "下载卡死阈值（秒）", scale=self.scale, width=14,
                             hint="服务器不再发数据超过该秒数就放弃该文件、继续下一个"
                                  "（最低 5 秒）")
        self.f_stall.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=(8, 0))

        # ---------- 资源保护
        res = Card(body, scale=self.scale)
        res.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        for col in range(3):
            res.columnconfigure(col, weight=1)

        ttk.Label(res, text="资源保护", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )

        self.f_maxfile = Field(res, "单文件体积上限（MB）", scale=self.scale, width=14,
                               hint="0 表示不限；超过则跳过并记录原因")
        self.f_maxfile.grid(row=1, column=0, sticky="ew", padx=(0, 10))

        self.f_minfree = Field(res, "低于该剩余磁盘（GB）则暂停", scale=self.scale,
                               width=14, hint="保护部署方的电脑不被写满")
        self.f_minfree.grid(row=1, column=1, sticky="ew", padx=(0, 10))

        self.f_logdays = Field(res, "日志保留天数", scale=self.scale, width=14)
        self.f_logdays.grid(row=1, column=2, sticky="ew")

        pick = PathPicker(res, "临时下载目录", mode="dir", card=True)
        pick.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.pick_temp = pick

        # ---------- 路径与自检
        diag = Card(body, scale=self.scale)
        diag.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        diag.columnconfigure(0, weight=1)

        head = ttk.Frame(diag, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="运行环境与自检", style="CardHead.TLabel").pack(side="left")
        ttk.Button(head, text="↻  重新自检", style="Ghost.TButton",
                   command=self._refresh_health).pack(side="right")

        paths = ttk.Frame(diag, style="Card.TFrame")
        paths.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(paths, text="打开数据目录", style="Ghost.TButton",
                   command=lambda: self._open("data")).pack(side="left")
        ttk.Button(paths, text="打开日志目录", style="Ghost.TButton",
                   command=lambda: self._open("logs")).pack(side="left", padx=(8, 0))
        ttk.Button(paths, text="打开临时目录", style="Ghost.TButton",
                   command=lambda: self._open("temp")).pack(side="left", padx=(8, 0))

        self.health_body = ttk.Frame(diag, style="Card.TFrame")
        self.health_body.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        Hint(diag,
             "本工具不向任何第三方服务器上报数据；所有处理都在本机完成。"
             "「立即跑一轮」可在正式启动前验证配置是否正确。",
             scale=self.scale).grid(row=3, column=0, sticky="w", pady=(10, 0))

        self.refresh()

    # -------------------------------------------------- 刷新

    def refresh(self) -> None:
        cfg = self.controller.config
        m = cfg.monitor

        self.f_poll.set(str(m.poll_interval_sec))
        self.f_jitter.set(str(m.jitter_sec))
        self.f_conc.set(str(m.download_concurrency))
        self.f_page.set(str(m.page_size))
        self.var_recursive.set(bool(m.recursive_folders))

        self.f_retries.set(str(m.max_retries))
        self.f_backoff.set(str(m.retry_backoff_sec))
        self.f_keep.set(str(m.keep_local_days))
        self.f_stall.set(str(getattr(m, "stall_timeout_sec", 20.0)))

        self.f_maxfile.set(str(m.max_file_mb or 0))
        self.f_minfree.set(str(m.min_free_disk_gb or 0))
        self.f_logdays.set(str(cfg.log_keep_days))

        if not self.pick_temp.get():
            self.pick_temp.set(cfg.temp_dir)

        self._refresh_health()

    def on_save(self) -> None:
        cfg = self.controller.config
        m = cfg.monitor

        def as_int(field: Field, default: int, minimum: int = 0) -> int:
            try:
                return max(minimum, int(float(field.get() or default)))
            except ValueError:
                return default

        def as_float(field: Field, default: float = 0.0) -> float:
            try:
                return max(0.0, float(field.get() or default))
            except ValueError:
                return default

        m.poll_interval_sec = as_int(self.f_poll, 300, 30)
        m.jitter_sec = as_int(self.f_jitter, 30)
        m.download_concurrency = as_int(self.f_conc, 1, 1)
        m.page_size = as_int(self.f_page, 50, 1)
        m.recursive_folders = bool(self.var_recursive.get())

        m.max_retries = as_int(self.f_retries, 3, 1)
        m.retry_backoff_sec = as_int(self.f_backoff, 30)
        m.keep_local_days = as_int(self.f_keep, 0)
        m.stall_timeout_sec = max(5.0, as_float(self.f_stall, 20.0))

        m.max_file_mb = as_float(self.f_maxfile)
        m.min_free_disk_gb = as_float(self.f_minfree)
        cfg.log_keep_days = as_int(self.f_logdays, 14, 1)

        cfg.temp_dir = self.pick_temp.get()

    # -------------------------------------------------- 自检

    def _refresh_health(self) -> None:
        for child in self.health_body.winfo_children():
            child.destroy()

        try:
            data = self.controller.health()
        except Exception as exc:
            ttk.Label(self.health_body, text=f"自检失败：{exc}",
                      style="CardMuted.TLabel").pack(anchor="w")
            return

        creds = data.get("credentials", {})
        napcat = data.get("napcat", {})
        problems = data.get("problems") or []

        rows = [
            ("配置校验", "✅ 通过" if not problems else "⚠ " + "；".join(problems)),
            ("已配置群数", str(data.get("groups", 0))),
            ("上传方式", f"{data.get('adapter')} → {data.get('target')}"),
            ("凭据加密后端", str(creds.get("backend", "—"))),
            ("已保存凭据条数", str(creds.get("count", 0))),
            ("数据目录", str(data.get("data_dir", "—"))),
            ("数据目录来源", str(data.get("data_dir_source", "—"))),
            ("QQ 组件", "✅ 运行中" if napcat.get("running")
             else ("已安装未运行" if napcat.get("installed") else "未安装")),
            ("监控状态", {"running": "运行中", "paused": "已暂停",
                          "stopped": "已停止"}.get(data.get("monitor", ""), "—")),
        ]
        for key, value in rows:
            KVRow(self.health_body, key, value).pack(fill="x")

    def _open(self, which: str) -> None:
        try:
            path = self.controller.open_path(which)
        except Exception:
            path = ""
        if not path or not os.path.exists(path):
            self.toast("目录尚不存在", "warning")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            self.toast(f"打开失败：{exc}", "error")

    def handle_event(self, event: Event) -> None:
        if event.kind in ("state", "login"):
            self._refresh_health()
