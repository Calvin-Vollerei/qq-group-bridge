"""QQ 登录页：NapCat 托管、二维码扫码、连接参数。"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..controller import Event
from .base import Tab
from .theme import Palette, ui_font
from .widgets import Card, Field, Hint

__all__ = ["QQTab"]


class QQTab(Tab):
    title = "QQ 登录"

    def build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ---------- 状态
        status = Card(self, scale=self.scale)
        status.grid(row=0, column=0, sticky="ew")

        head = ttk.Frame(status, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="QQ 组件（NapCat）", style="CardHead.TLabel").pack(side="left")
        self.badge = tk.Label(head, text="未知", bg=Palette.DISABLED, fg="#FFFFFF",
                              padx=10, pady=2, font=ui_font(self.scale, size=9, bold=True))
        self.badge.pack(side="left", padx=(10, 0))

        # 二维码刷新状态：允许连点（新请求取代旧请求），期间按钮置忙
        self._qr_pending = False
        self._qr_image = None
        self._poll_left = 0

        self.status_text = ttk.Label(status, text="", style="CardMuted.TLabel",
                                     justify="left")
        self.status_text.pack(anchor="w", pady=(8, 0))

        btns = ttk.Frame(status, style="Card.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        self.btn_start = ttk.Button(btns, text="启动组件", style="Accent.TButton",
                                    command=self._start)
        self.btn_start.pack(side="left")
        ttk.Button(btns, text="停止", style="Ghost.TButton",
                   command=self._stop).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="刷新状态", style="Ghost.TButton",
                   command=self.refresh).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="从压缩包安装…", style="Ghost.TButton",
                   command=self._install).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="打开安装目录", style="Ghost.TButton",
                   command=self._open_dir).pack(side="left", padx=(8, 0))

        # ---------- 二维码 + 安装引导（左右两栏）
        middle = ttk.Frame(self)
        middle.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        middle.columnconfigure(0, weight=0)
        middle.columnconfigure(1, weight=1)

        qr_card = Card(middle, scale=self.scale)
        qr_card.grid(row=0, column=0, sticky="nsew")

        ttk.Label(qr_card, text="扫码登录", style="CardHead.TLabel").pack(anchor="w")

        self.qr_label = tk.Label(qr_card, text="尚未获取二维码", width=26, height=12,
                                 bg="#FFFFFF", fg=Palette.MUTED, relief="flat",
                                 font=ui_font(self.scale, size=9))
        self.qr_label.pack(pady=(8, 8))

        self.qr_hint = ttk.Label(qr_card, text="", style="CardMuted.TLabel",
                                 wraplength=220, justify="left")
        self.qr_hint.pack(anchor="w")

        qr_btns = ttk.Frame(qr_card, style="Card.TFrame")
        qr_btns.pack(fill="x", pady=(10, 0))
        self.btn_qr = ttk.Button(qr_btns, text="① 获取/刷新二维码", style="Accent.TButton",
                                 command=self._fetch_qr)
        self.btn_qr.pack(fill="x")
        ttk.Button(qr_btns, text="② 我已扫码，检查登录状态", style="Ghost.TButton",
                   command=self._probe).pack(fill="x", pady=(6, 0))
        ttk.Button(qr_btns, text="🌐  打开 NapCat 网页版（备用扫码入口）",
                   style="Ghost.TButton",
                   command=self._open_webui).pack(fill="x", pady=(6, 0))
        ttk.Button(qr_btns, text="保存二维码为图片…", style="Ghost.TButton",
                   command=self._save_qr).pack(fill="x", pady=(6, 0))

        guide_card = Card(middle, scale=self.scale)
        guide_card.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        guide_card.rowconfigure(1, weight=1)
        guide_card.columnconfigure(0, weight=1)

        ttk.Label(guide_card, text="首次使用向导", style="CardHead.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.guide = tk.Text(guide_card, height=14, wrap="word", relief="flat",
                             bg=Palette.CARD, fg=Palette.TEXT,
                             font=ui_font(self.scale, size=10), padx=0)
        self.guide.grid(row=1, column=0, sticky="nsew")
        self.guide.configure(state="disabled")

        # ---------- 连接参数
        params = Card(self, scale=self.scale)
        params.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        params.columnconfigure(0, weight=1)
        params.columnconfigure(1, weight=1)
        params.columnconfigure(2, weight=1)

        ttk.Label(params, text="连接参数", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )

        self.f_api = Field(params, "OneBot API 地址", scale=self.scale,
                           hint="NapCat 的 HTTP 服务地址，默认 127.0.0.1:3000", width=26)
        self.f_api.grid(row=1, column=0, sticky="ew", padx=(0, 10))

        self.f_webui = Field(params, "WebUI 地址", scale=self.scale,
                             hint="用于获取登录二维码，默认 127.0.0.1:6099", width=26)
        self.f_webui.grid(row=1, column=1, sticky="ew", padx=(0, 10))

        self.f_token = Field(params, "OneBot 访问令牌（可留空）", scale=self.scale,
                             hint="留空表示 NapCat 未开启鉴权", width=26, show="•")
        self.f_token.grid(row=1, column=2, sticky="ew")

        self.f_webui_token = Field(params, "WebUI 令牌", scale=self.scale,
                                   hint="可从 NapCat 配置自动读取", width=26, show="•")
        self.f_webui_token.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=(10, 0))

        ttk.Button(params, text="保存令牌（加密）", style="Ghost.TButton",
                   command=self._save_tokens).grid(row=2, column=1, sticky="w",
                                                   pady=(10, 0))
        ttk.Button(params, text="从 NapCat 配置自动读取", style="Ghost.TButton",
                   command=self._discover_token).grid(row=2, column=2, sticky="w",
                                                      pady=(10, 0))

        Hint(params,
             "令牌一律以 Windows DPAPI 加密后保存，绑定本机当前用户；"
             "不会写入配置文件，也不会出现在日志里。",
             scale=self.scale).grid(row=3, column=0, columnspan=3, sticky="w",
                                    pady=(10, 0))

        self.refresh()

    # -------------------------------------------------- 刷新

    def refresh(self) -> None:
        from ..napcat.process import is_admin, resolve_qq_path

        status = self.controller.napcat_status()
        installed = bool(status.get("installed"))
        running = bool(status.get("running"))

        if running:
            self.badge.configure(text="运行中", bg=Palette.ACCENT)
        elif installed:
            self.badge.configure(text="已安装", bg=Palette.WARN)
        else:
            self.badge.configure(text="未安装", bg=Palette.DISABLED)

        lines = [
            f"安装目录：{status.get('install_dir') or '—'}",
            f"启动器：{status.get('launcher') or '—'}",
        ]
        if status.get("pid"):
            lines.append(f"进程号：{status['pid']}")
        if status.get("note"):
            lines.append(f"说明：{status['note']}")

        # 挂钩模式的两项关键前提，直接摆在状态里 —— 否则失败时会毫无线索
        admin = is_admin()
        hook_dir = Path(status.get("install_dir") or "") / "shell"
        if (hook_dir / "NapCatWinBootMain.exe").is_file():
            qq = resolve_qq_path(self.controller.config.napcat.qq_path)
            lines.append(f"QQ 入口：{qq or '未找到（注册表里也没有）'}")
            if not admin:
                lines.append(
                    "⚠ 当前非管理员：登录 QQ 需要管理员权限，否则会静默失败。"
                    "请用「以管理员身份启动.bat」重新打开本程序。"
                )
            else:
                lines.append("✅ 管理员权限已具备")

        self.status_text.configure(text="\n".join(lines))

        self.btn_start.state(["disabled"] if running or not installed else ["!disabled"])

        cfg = self.controller.config
        self.f_api.set(cfg.napcat.api_base)
        self.f_webui.set(cfg.napcat.webui_base)

        self._set_guide(self.controller.napcat_install_hint())

        if not self.f_token.get() or not self.f_webui_token.get():
            overview = {r["key"]: r["masked"] for r in self.controller.credentials_overview()}
            from ..secrets import KEY_NAPCAT_WEBUI_TOKEN, KEY_QQ_ONEBOT_TOKEN

            if overview.get(KEY_QQ_ONEBOT_TOKEN) and not self.f_token.get():
                self.f_token.entry.configure(show="")
                self.f_token.set(f"（已保存：{overview[KEY_QQ_ONEBOT_TOKEN]}，留空则不修改）")
                self.f_token.entry.configure(state="readonly")
            if overview.get(KEY_NAPCAT_WEBUI_TOKEN) and not self.f_webui_token.get():
                self.f_webui_token.entry.configure(show="")
                self.f_webui_token.set(f"（已保存：{overview[KEY_NAPCAT_WEBUI_TOKEN]}，留空则不修改）")
                self.f_webui_token.entry.configure(state="readonly")

    def _set_guide(self, text: str) -> None:
        self.guide.configure(state="normal")
        self.guide.delete("1.0", "end")
        self.guide.insert("1.0", text)
        self.guide.configure(state="disabled")

    # -------------------------------------------------- 保存

    def on_save(self) -> None:
        cfg = self.controller.config
        api = self.f_api.get()
        webui = self.f_webui.get()
        if api:
            cfg.napcat.api_base = api
        if webui:
            cfg.napcat.webui_base = webui

    # -------------------------------------------------- 动作

    def _start(self) -> None:
        if self.controller.start_napcat():
            self.toast("正在启动 QQ 组件…")

    def _stop(self) -> None:
        if self.controller.stop_napcat():
            self.toast("正在停止 QQ 组件…")

    def _install(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 NapCat 压缩包",
            filetypes=[("压缩包", "*.zip"), ("全部文件", "*.*")],
        )
        if not path:
            return
        if messagebox.askyesno(
            "确认安装",
            "将从所选压缩包解压安装 QQ 组件到：\n\n"
            f"{self.controller.napcat_status().get('install_dir')}\n\n"
            "已有内容不会被删除。是否继续？",
        ):
            if self.controller.install_napcat_from_zip(path):
                self.toast("正在安装…")

    def _open_dir(self) -> None:
        import os
        import subprocess
        import sys

        path = self.controller.napcat_status().get("install_dir")
        if not path or not os.path.isdir(path):
            messagebox.showinfo("提示", "安装目录尚不存在。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def _fetch_qr(self) -> None:
        """获取/刷新二维码。

        可以**反复点**：新的请求会取代上一次还没回来的请求（旧结果直接丢弃）。
        旧版这里在拿不到执行权时什么都不做，用户看到的就是「点了没反应」——
        而单次取码最长 25 秒、二维码 30 秒过期，连点是必然行为。
        """
        self._qr_pending = True
        self.set_busy(self.btn_qr, True, "① 获取/刷新二维码")
        self.qr_hint.configure(text="正在获取二维码…（最长等 25 秒）")
        if not self.controller.fetch_qrcode():
            self._qr_pending = False
            self.set_busy(self.btn_qr, False)
            self.toast("获取二维码的请求未能启动，请稍后重试", "warning")

    def _save_qr(self) -> None:
        qr = self.controller.qrcode
        if not qr.ok:
            messagebox.showinfo("提示", "请先获取二维码。")
            return
        path = filedialog.asksaveasfilename(
            title="保存二维码", defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")], initialfile="qq-qrcode.png",
        )
        if path:
            if qr.save(path):
                messagebox.showinfo("已保存", f"二维码已保存到：\n{path}")
            else:
                messagebox.showerror("保存失败", "二维码内容不可用。")

    def _probe(self) -> None:
        if self.controller.probe_qq():
            self.toast("正在检查登录状态…")

    def _open_webui(self) -> None:
        """在浏览器打开 NapCat 网页版（备用扫码入口）。"""
        import webbrowser

        url = self.controller.napcat_webui_url()
        if not url:
            messagebox.showinfo("提示", "请先填写 WebUI 地址（默认 127.0.0.1:6099）。")
            return
        try:
            webbrowser.open(url)
            self.toast("已在浏览器打开 NapCat 网页版", "info")
        except (OSError, webbrowser.Error) as exc:
            messagebox.showerror(
                "打开失败", f"{exc}\n\n请手动在浏览器访问：\n{url.split('?')[0]}"
            )

    def _save_tokens(self) -> None:
        from ..secrets import KEY_NAPCAT_WEBUI_TOKEN, KEY_QQ_ONEBOT_TOKEN

        secrets = self.controller.secrets
        if secrets is None:
            messagebox.showerror("不可用", "凭据库不可用，无法保存令牌。")
            return
        saved = 0
        for widget, key in ((self.f_token, KEY_QQ_ONEBOT_TOKEN),
                            (self.f_webui_token, KEY_NAPCAT_WEBUI_TOKEN)):
            value = widget.get()
            if value and not value.startswith("（已保存"):
                secrets.set(key, value)
                saved += 1
        if saved:
            self.toast(f"已加密保存 {saved} 项令牌", "success")
        else:
            self.toast("没有需要保存的新令牌", "muted")

    def _discover_token(self) -> None:
        if self.controller.discover_webui_token():
            self.toast("正在读取 NapCat 配置…")

    # -------------------------------------------------- 事件

    def handle_event(self, event: Event) -> None:
        if event.kind == "login":
            # 扫码成功后自动停止轮询，并给出明确的下一步提示
            if event.data.get("category") == "online":
                self._poll_left = 0
                self.qr_hint.configure(
                    text="✅ 已登录成功。现在回到「监控」页点「▶ 开始监控」即可。"
                )
                self.toast("QQ 登录成功，可以开始监控了", "success")
            self.refresh()
            return

        if event.kind == "napcat":
            self.refresh()
            return

        if event.kind == "qr":
            self._render_qr(event)
            return

        if event.kind == "creds":
            self.refresh()

    # -------------------------------------------------- 扫码后轮询

    _poll_left = 0

    def _start_login_poll(self, times: int = 30) -> None:
        """扫码后自动轮询登录状态（走后台线程，不阻塞界面）。"""
        self._poll_left = times
        self.after(2500, self._tick_poll)

    def _tick_poll(self) -> None:
        if self._poll_left <= 0:
            return
        self._poll_left -= 1
        self.controller.probe_qq()          # 异步；结果经 login 事件回来
        self.after(3000, self._tick_poll)

    def _render_qr(self, event: Event) -> None:
        # pending = 「正在获取…」的即时反馈事件：只更新提示，**不要**动已有二维码
        if event.data.get("pending"):
            self.qr_hint.configure(text="正在获取二维码…（最长等 25 秒，可重复点击刷新）")
            return

        self._qr_pending = False
        self.set_busy(self.btn_qr, False)

        qr = self.controller.qrcode
        if qr.ok:
            try:
                from io import BytesIO

                from PIL import Image, ImageTk

                image = Image.open(BytesIO(qr.png_bytes))
                size = int(220 * self.scale)
                image.thumbnail((size, size))
                self._qr_image = ImageTk.PhotoImage(image)
                self.qr_label.configure(image=self._qr_image, text="", width=0, height=0,
                                        bg="#FFFFFF")
                self.qr_hint.configure(
                    text="请用手机 QQ 扫码登录。二维码约 30 秒失效 —— "
                         "过期就点「① 获取/刷新二维码」重新取一张（可连点）。\n"
                         "扫码后本页会自动检测登录结果。"
                )
                self._start_login_poll()
                return
            except Exception as exc:  # Pillow 缺失或图片异常
                self.qr_hint.configure(
                    text=f"无法在界面显示二维码（{type(exc).__name__}）。"
                         "请点「保存二维码为图片」后用手机扫描。"
                )
                return

        self.qr_label.configure(image="", text="二维码不可用", width=26, height=12,
                                bg="#FFF7E6", fg=Palette.WARN)
        # 失败时给出「原因 + 下一步怎么做」，而不是只甩一句错误
        reason = event.message or "获取失败"
        hint = str(event.data.get("hint") or "")
        retry = "可以再点一次「① 获取/刷新二维码」重试。"
        parts = [reason]
        if hint:
            parts.append(hint)
        parts.append(retry)
        self.qr_hint.configure(text="\n".join(parts))
