"""网盘页：上传目标配置、凭据管理、连通性测试。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, ttk

from ..controller import Event
from ..naming import FOLDER_STYLES
from ..errors import QgbError
from .base import ScrollFrame, Tab
from .theme import Palette, ui_font
from .widgets import Card, Field, Hint, PathPicker

__all__ = ["NetdiskTab"]


class NetdiskTab(Tab):
    title = "网盘与凭据"

    def build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # 本页卡片较多，小窗口下会超出可视区，因此套一层滚动容器
        # （与「群与规则」「高级」两页保持同样的处理方式）
        scroller = ScrollFrame(self)
        scroller.grid(row=0, column=0, sticky="nsew")
        body = scroller.inner
        body.columnconfigure(0, weight=1)

        # ---------- 上传目标
        target = Card(body, scale=self.scale)
        target.grid(row=0, column=0, sticky="ew")
        target.columnconfigure(0, weight=1)
        target.columnconfigure(1, weight=1)

        ttk.Label(target, text="上传目标", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )

        ttk.Label(target, text="上传方式", style="Card.TLabel").grid(row=1, column=0,
                                                                    sticky="w")
        self.adapter = ttk.Combobox(target, state="readonly", width=34)
        self.adapter.grid(row=2, column=0, sticky="ew", padx=(0, 10))
        self.adapter.bind("<<ComboboxSelected>>", lambda _e: self._on_adapter_change())

        self.f_root = Field(target, "网盘目标目录", scale=self.scale,
                            hint="例如 /QQ群备份", width=30)
        self.f_root.grid(row=1, column=1, rowspan=2, sticky="ew")

        # WebDAV 区
        self.webdav_box = ttk.Frame(target, style="Card.TFrame")
        self.webdav_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        # 标记为「按条件显示」：scripts/gui_inspect.py 据此豁免「未放置」告警，
        # 否则按上传方式切换显隐的容器会被误判成 bug。
        self.webdav_box.qgb_conditional = True
        self.webdav_box.columnconfigure(0, weight=1)
        self.webdav_box.columnconfigure(1, weight=1)

        self.f_dav_url = Field(self.webdav_box, "WebDAV 地址", scale=self.scale,
                               hint="OpenList 的 WebDAV 入口，通常是 …/dav", width=30)
        self.f_dav_url.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        self.f_dav_user = Field(self.webdav_box, "OpenList 用户名", scale=self.scale,
                                hint="留空表示匿名访问", width=30)
        self.f_dav_user.grid(row=0, column=1, sticky="ew")

        self.f_dav_pass = Field(self.webdav_box, "OpenList 密码", scale=self.scale,
                               hint="以 DPAPI 加密保存，仅本机可解密", width=30, show="•")
        self.f_dav_pass.grid(row=1, column=0, sticky="ew", padx=(0, 10), pady=(10, 0))

        self.dav_btns = ttk.Frame(self.webdav_box, style="Card.TFrame")
        self.dav_btns.grid(row=1, column=1, sticky="sw", pady=(10, 0))
        ttk.Button(self.dav_btns, text="保存凭据", style="Ghost.TButton",
                   command=self._save_creds).pack(side="left")
        ttk.Button(self.dav_btns, text="清除凭据", style="Ghost.TButton",
                   command=self._clear_creds).pack(side="left", padx=(8, 0))

        # 本地目录区
        self.local_box = ttk.Frame(target, style="Card.TFrame")
        self.local_box.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.local_box.qgb_conditional = True  # 同上：按上传方式显隐
        self.local_box.columnconfigure(0, weight=1)
        self.pick_local = PathPicker(self.local_box, "本地目标目录（同步盘文件夹）",
                                     mode="dir", card=True)
        self.pick_local.grid(row=0, column=0, sticky="ew")

        # 选项
        opts = ttk.Frame(target, style="Card.TFrame")
        opts.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.var_split = tk.BooleanVar(value=True)
        self.var_verify = tk.BooleanVar(value=True)
        self.var_manage_ol = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="按群号分子目录", variable=self.var_split,
                        style="Card.TCheckbutton").pack(side="left")
        ttk.Checkbutton(opts, text="上传后回读校验大小", variable=self.var_verify,
                        style="Card.TCheckbutton").pack(side="left", padx=(16, 0))
        ttk.Checkbutton(opts, text="随程序自动启停 OpenList", variable=self.var_manage_ol,
                        style="Card.TCheckbutton").pack(side="left", padx=(16, 0))

        # 群目录命名风格：决定 <远端根目录>\<这里>\<文件名> 中间那一段叫什么
        style_row = ttk.Frame(target, style="Card.TFrame")
        style_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(style_row, text="群目录命名", style="Card.TLabel").pack(side="left")
        self._style_ids = list(FOLDER_STYLES.keys())
        self.style = ttk.Combobox(
            style_row,
            values=[FOLDER_STYLES[k] for k in self._style_ids],
            state="readonly", width=34,
        )
        self.style.pack(side="left", padx=(10, 0))
        Hint(style_row, "只影响子目录名；也可到「群与规则」页给单个群指定名字",
             scale=self.scale).pack(side="left", padx=(10, 0))

        actions = ttk.Frame(target, style="Card.TFrame")
        actions.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="💾  保存设置", style="Accent.TButton",
                   command=self._save_settings).pack(side="left")
        self.btn_test = ttk.Button(actions, text="🔌  测试连接", style="Ghost.TButton",
                                   command=self._test)
        self.btn_test.pack(side="left", padx=(8, 0))

        Hint(target,
             "推荐链路：本工具 → OpenList（WebDAV）→ 百度网盘。"
             "把后端换成别的网盘时，只需在 OpenList 网页端改驱动，本工具无需改动。",
             scale=self.scale).grid(row=8, column=0, columnspan=2, sticky="w",
                                    pady=(10, 0))

        # ---------- 打开网盘（自动探测安装位置）
        cloud = Card(body, scale=self.scale)
        cloud.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        cloud.columnconfigure(0, weight=1)

        cloud_head = ttk.Frame(cloud, style="Card.TFrame")
        cloud_head.grid(row=0, column=0, sticky="ew")
        ttk.Label(cloud_head, text="打开网盘看上传结果",
                  style="CardHead.TLabel").pack(side="left")
        ttk.Button(cloud_head, text="🔄  重新检测", style="Ghost.TButton",
                   command=self._detect_clients).pack(side="right")

        self.cloud_body = ttk.Frame(cloud, style="Card.TFrame")
        self.cloud_body.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        Hint(cloud,
             "按钮会自动读取本机网盘的安装位置（注册表 → 常见目录 → 盘符扫描）。"
             "装了客户端就直接打开客户端；没装就在浏览器打开网页版 —— "
             "不需要你手动找路径，也不需要先登录。",
             scale=self.scale).grid(row=2, column=0, sticky="w", pady=(8, 0))

        # ---------- OpenList（WebDAV 中转）
        ol = Card(body, scale=self.scale)
        ol.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ol.columnconfigure(0, weight=1)

        ol_head = ttk.Frame(ol, style="Card.TFrame")
        ol_head.grid(row=0, column=0, sticky="ew")
        ttk.Label(ol_head, text="OpenList（WebDAV 中转）",
                  style="CardHead.TLabel").pack(side="left")
        ttk.Button(ol_head, text="🔄  刷新状态", style="Ghost.TButton",
                   command=self._refresh_openlist).pack(side="right")

        self.ol_text = ttk.Label(ol, text="", style="CardMuted.TLabel",
                                 justify="left", wraplength=int(700 * self.scale))
        self.ol_text.grid(row=1, column=0, sticky="w", pady=(8, 0))

        ol_btns = ttk.Frame(ol, style="Card.TFrame")
        ol_btns.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(ol_btns, text="▶  启动 OpenList", style="Ghost.TButton",
                   command=self._start_openlist).pack(side="left")
        ttk.Button(ol_btns, text="⏹  停止（仅限本程序启动的）", style="Ghost.TButton",
                   command=self._stop_openlist).pack(side="left", padx=(8, 0))
        ttk.Button(ol_btns, text="🌐  打开管理后台", style="Ghost.TButton",
                   command=self._open_openlist).pack(side="left", padx=(8, 0))

        Hint(ol,
             "OpenList 是独立程序，负责把文件真正传到百度网盘。"
             "勾选下面的选项后，本程序会在启动时自动拉起它、退出时自动关掉 —— "
             "但**只动自己启动的那一个**，你自己开的实例不会被关闭。",
             scale=self.scale).grid(row=3, column=0, sticky="w", pady=(8, 0))

        # ---------- 本机凭据
        creds = Card(body, scale=self.scale)
        creds.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        creds.columnconfigure(0, weight=1)

        head = ttk.Frame(creds, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="本机保存的凭据", style="CardHead.TLabel").pack(side="left")
        ttk.Button(head, text="🗑  清除本机全部凭据", style="Danger.TButton",
                   command=self._clear_all).pack(side="right")

        self.cred_body = ttk.Frame(creds, style="Card.TFrame")
        self.cred_body.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.cred_note = ttk.Label(creds, text="", style="CardMuted.TLabel",
                                   justify="left")
        self.cred_note.grid(row=2, column=0, sticky="w", pady=(8, 0))

        self.refresh()
        self._detect_clients()

    # -------------------------------------------------- 刷新

    def refresh(self) -> None:
        cfg = self.controller.config

        choices = self.controller.adapter_choices()
        self._adapter_ids = [cid for cid, _ in choices]
        self.adapter.configure(values=[f"{label}" for _, label in choices])
        current = cfg.upload.adapter
        index = self._adapter_ids.index(current) if current in self._adapter_ids else 0
        self.adapter.current(index)

        self.f_root.set(cfg.upload.remote_root)
        self.f_dav_url.set(cfg.upload.webdav_url)
        self.pick_local.set(cfg.upload.local_root)
        self.var_split.set(bool(cfg.upload.split_by_group))
        self.var_verify.set(bool(cfg.upload.verify_after_upload))
        self.var_manage_ol.set(bool(cfg.upload.manage_openlist))
        _style = (cfg.upload.folder_style or "id").lower()
        self.style.current(self._style_ids.index(_style) if _style in self._style_ids else 0)

        # 已保存的用户名回显（密码永不回显）
        overview = {r["key"]: r["masked"] for r in self.controller.credentials_overview()}
        from ..secrets import KEY_NETDISK_WEBDAV_PASSWORD, KEY_NETDISK_WEBDAV_USERNAME

        if overview.get(KEY_NETDISK_WEBDAV_USERNAME) and not self.f_dav_user.get():
            self.f_dav_user.set(overview[KEY_NETDISK_WEBDAV_USERNAME])
        if overview.get(KEY_NETDISK_WEBDAV_PASSWORD) and not self.f_dav_pass.get():
            self.f_dav_pass.set("（已保存，留空则不修改）")

        self._on_adapter_change()
        self._refresh_openlist()
        self._render_credentials()

    def _on_adapter_change(self) -> None:
        index = self.adapter.current()
        adapter_id = self._adapter_ids[index] if 0 <= index < len(self._adapter_ids) else "webdav"
        if adapter_id == "webdav":
            self.local_box.grid_remove()
            self.webdav_box.grid()
        else:
            self.webdav_box.grid_remove()
            self.local_box.grid()

    def _render_credentials(self) -> None:
        for child in self.cred_body.winfo_children():
            child.destroy()

        rows = self.controller.credentials_overview()
        health = self.controller.credentials_health()

        if not rows:
            ttk.Label(self.cred_body, text="（尚未保存任何凭据）",
                      style="CardMuted.TLabel").pack(anchor="w")
        else:
            for row in rows:
                line = ttk.Frame(self.cred_body, style="Card.TFrame")
                line.pack(fill="x", pady=1)
                ttk.Label(line, text=f"• {row['label']}", width=18,
                          style="Card.TLabel").pack(side="left")
                ttk.Label(line, text=row["masked"], style="CardMuted.TLabel").pack(side="left")

        self.cred_note.configure(
            text=f"加密后端：{health.get('backend')}\n"
                 f"存储位置：{health.get('path')}\n"
                 "凭据与本机当前 Windows 用户绑定，复制到其他电脑无法解密。"
                 "界面只显示遮蔽值，程序不采集账号昵称、会员状态或容量信息。"
        )

    # -------------------------------------------------- 保存

    def on_save(self) -> None:
        cfg = self.controller.config
        index = self.adapter.current()
        if 0 <= index < len(self._adapter_ids):
            cfg.upload.adapter = self._adapter_ids[index]

        cfg.upload.remote_root = self.f_root.get() or "/QQ群备份"
        cfg.upload.webdav_url = self.f_dav_url.get() or cfg.upload.webdav_url
        cfg.upload.local_root = self.pick_local.get()
        cfg.upload.split_by_group = bool(self.var_split.get())
        cfg.upload.verify_after_upload = bool(self.var_verify.get())
        cfg.upload.manage_openlist = bool(self.var_manage_ol.get())
        _idx = self.style.current()
        if 0 <= _idx < len(self._style_ids):
            cfg.upload.folder_style = self._style_ids[_idx]

    # -------------------------------------------------- 动作

    def _save_settings(self) -> None:
        if self.app is not None:
            self.app.save_all()

    def _save_creds(self) -> None:
        user = self.f_dav_user.get()
        pwd = self.f_dav_pass.get()
        if pwd.startswith("（已保存"):
            pwd = ""
        if not user and not pwd:
            messagebox.showinfo("提示", "没有需要保存的凭据。")
            return
        try:
            self.controller.save_webdav_credentials(user, pwd)
            self.f_dav_pass.set("")
            self.toast("网盘凭据已加密保存", "success")
            self.refresh()
        except QgbError as exc:
            messagebox.showerror(exc.title, f"{exc.message}\n\n{exc.hint}")

    def _clear_creds(self) -> None:
        if not messagebox.askyesno("确认", "确定要清除网盘凭据吗？清除后需要重新授权。"):
            return
        self.controller.clear_webdav_credentials()
        self.f_dav_user.set("")
        self.f_dav_pass.set("")
        self.refresh()

    def _test(self) -> None:
        if self.app is not None:
            self.app.save_all()
        if self.controller.test_netdisk():
            self.toast("正在测试连接…")

    # -------------------------------------------------- 打开网盘

    def _detect_clients(self) -> None:
        """探测本机网盘客户端，并把结果画成按钮。

        路径来自注册表 ``InstallLocation`` / ``DisplayIcon``，找不到再退回
        常见目录与盘符扫描 —— 用户把网盘装在哪个盘都能认出来。
        """
        for child in self.cloud_body.winfo_children():
            child.destroy()

        clients = self.controller.netdisk_clients()
        if not clients:
            ttk.Label(
                self.cloud_body,
                text="没有检测到已安装的网盘客户端 —— 点下面的按钮会在浏览器打开网页版。",
                style="CardMuted.TLabel",
                wraplength=int(560 * self.scale),
                justify="left",
            ).pack(anchor="w")
            ttk.Button(self.cloud_body, text="🌐  打开百度网盘网页版",
                       style="Accent.TButton",
                       command=lambda: self._open_cloud("baidu")).pack(
                anchor="w", pady=(8, 0))
            return

        for client in clients:
            row = ttk.Frame(self.cloud_body, style="Card.TFrame")
            row.pack(fill="x", pady=(0, 6))

            installed = client["installed"]
            label = ("📂  " if installed else "🌐  ") + f"打开{client['name']}"
            style = "Accent.TButton" if client["key"] == "baidu" else "Ghost.TButton"
            ttk.Button(row, text=label, style=style,
                       command=lambda k=client["key"]: self._open_cloud(k)).pack(
                side="left")

            # 把探测到的真实路径显示出来 —— 用户能一眼确认对不对
            if installed:
                detail = f"{client['display_path']}   （来源：{client['source']}）"
            else:
                detail = "未安装客户端，将打开网页版"
            ttk.Label(row, text=detail, style="CardMuted.TLabel").pack(
                side="left", padx=(10, 0))

            for sync_dir in client["sync_dirs"][:1]:
                ttk.Button(
                    row, text="📁  打开同步文件夹", style="Ghost.TButton",
                    command=lambda p=sync_dir: self._open_folder(p),
                ).pack(side="right")

    def _open_folder(self, path: str) -> None:
        resolved = self.controller.open_path(path)
        if not resolved:
            messagebox.showinfo("提示", f"目录不存在：\n{path}")
            return
        try:
            os.startfile(resolved)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc))

    def _open_cloud(self, key: str) -> None:
        if self.controller.open_netdisk(key):
            self.toast("正在打开网盘…")

    # -------------------------------------------------- OpenList

    def _refresh_openlist(self) -> None:
        st = self.controller.openlist_status()
        if not st:
            self.ol_text.configure(text="（无法获取 OpenList 状态）")
            return

        running = st.get("running")
        managed = st.get("managed")
        mark = "✅ 运行中" if running else "⏹ 未运行"
        if running and managed:
            mark += "（本程序启动）"
        lines = [
            f"状态：{mark}",
            f"地址：{st.get('webui_url') or '—'}   WebDAV：{st.get('webdav_url') or '—'}",
            f"目录：{st.get('install_dir') or '—'}",
        ]
        if st.get("note"):
            lines.append(f"说明：{st['note']}")
        self.ol_text.configure(text="\n".join(lines))

    def _start_openlist(self) -> None:
        if self.controller.start_openlist():
            self.toast("正在启动 OpenList…")

    def _stop_openlist(self) -> None:
        if self.controller.stop_openlist():
            self.toast("正在停止 OpenList…")

    def _open_openlist(self) -> None:
        import webbrowser

        url = self.controller.openlist_webui_url()
        if not url:
            messagebox.showinfo("提示", "无法确定 OpenList 地址。")
            return
        try:
            webbrowser.open(url)
            self.toast("已在浏览器打开 OpenList 管理后台", "info")
        except (OSError, webbrowser.Error) as exc:
            messagebox.showerror("打开失败", f"{exc}\n\n请手动访问：{url}")

    def _clear_all(self) -> None:
        if not messagebox.askyesno(
            "危险操作",
            "将清除本机保存的**全部**凭据（网盘账号、QQ 令牌等）。\n\n"
            "清除后需要重新授权。是否继续？",
        ):
            return
        self.controller.clear_all_credentials()
        self.f_dav_user.set("")
        self.f_dav_pass.set("")
        self.refresh()

    # -------------------------------------------------- 事件

    def handle_event(self, event: Event) -> None:
        if event.kind in ("netdisk", "creds"):
            self.refresh()
        if event.kind == "openlist":
            self._refresh_openlist()
            self.toast(event.message, "success" if event.data.get("ok") else "warning")
        if event.kind == "task:netdisk_test":
            self.set_busy(self.btn_test, False, "🔌  测试连接")
        if event.kind == "netdisk" and event.data.get("opened") == "web":
            self.toast(event.message, "info")
        elif event.kind == "netdisk" and event.data.get("opened") == "client":
            self.toast(event.message, "success")
