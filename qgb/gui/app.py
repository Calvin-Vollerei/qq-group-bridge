"""主窗口：标签页装配、事件泵、生命周期。"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

from ..controller import AppController, Event
from ..errors import QgbError
from ..version import APP_NAME, __version__
from .base import Tab
from .tab_advanced import AdvancedTab
from .tab_groups import GroupsTab
from .tab_monitor import MonitorTab
from .tab_netdisk import NetdiskTab
from .tab_qq import QQTab
from .theme import Palette, apply_theme, enable_dpi_awareness, ui_font

__all__ = ["MainWindow", "main"]

PUMP_INTERVAL_MS = 150


class MainWindow(tk.Tk):
    """应用主窗口。"""

    def __init__(self, controller: AppController | None = None) -> None:
        super().__init__()

        self.scale = enable_dpi_awareness()
        apply_theme(self, self.scale)

        self.title(f"{APP_NAME}  v{__version__}")
        self.geometry(self._centered_geometry(1040, 800))
        self.minsize(int(900 * self.scale), int(640 * self.scale))
        self.configure(bg=Palette.BG)

        self.controller = controller or AppController()
        self.controller.load()

        self._tabs: list[Tab] = []
        self._status_reset_at = 0.0

        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self._build_menu()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # 先排空启动阶段（加载配置/凭据）产生的事件，避免第一次刷新丢状态
        self._pump()
        self.after(PUMP_INTERVAL_MS, self._pump_loop)

        self._startup_checks()

    # ================================================================ 布局

    def _centered_geometry(self, width: int, height: int) -> str:
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = int(width * self.scale)
        h = int(height * self.scale)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        return f"{w}x{h}+{x}+{y}"

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(16, 12, 16, 6))
        header.pack(fill="x")

        left = ttk.Frame(header)
        left.pack(side="left")
        ttk.Label(left, text=f"📦  {APP_NAME}", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            left,
            text="把指定 QQ 群的新文件自动抓取、过滤并上传到网盘（推荐经 OpenList 中转）",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        right = ttk.Frame(header)
        right.pack(side="right")
        self.header_state = ttk.Label(right, text="已停止", style="Head.TLabel")
        self.header_state.pack(anchor="e")
        self.header_detail = ttk.Label(right, text="", style="Muted.TLabel")
        self.header_detail.pack(anchor="e", pady=(2, 0))

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        for cls in (MonitorTab, GroupsTab, QQTab, NetdiskTab, AdvancedTab):
            tab = cls(self.notebook, self.controller, scale=self.scale, app=self)
            self.notebook.add(tab, text=cls.title)
            self._tabs.append(tab)

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(16, 6, 16, 8))
        bar.pack(fill="x")

        self.status_text = ttk.Label(bar, text="就绪", style="Muted.TLabel")
        self.status_text.pack(side="left")

        ttk.Label(
            bar,
            text="凭据已加密保存 · 日志已脱敏 · 不上报任何数据",
            style="Muted.TLabel",
        ).pack(side="right")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="保存全部设置", command=self.save_all)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        monitor_menu = tk.Menu(menubar, tearoff=0)
        monitor_menu.add_command(label="开始监控", command=lambda: self._monitor("start"))
        monitor_menu.add_command(label="暂停 / 恢复", command=lambda: self._monitor("pause"))
        monitor_menu.add_command(label="停止监控", command=lambda: self._monitor("stop"))
        monitor_menu.add_separator()
        monitor_menu.add_command(label="立即跑一轮", command=lambda: self._monitor("once"))
        menubar.add_cascade(label="监控", menu=monitor_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="运行环境自检", command=self._show_health)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        try:
            self.configure(menu=menubar)
        except tk.TclError:
            pass

    # ================================================================ 事件泵

    def _pump_loop(self) -> None:
        self._pump()
        self.after(PUMP_INTERVAL_MS, self._pump_loop)

    def _pump(self) -> None:
        for event in self.controller.drain():
            try:
                self._dispatch(event)
            except Exception:
                # 单个事件处理失败绝不能让界面卡死
                import logging

                logging.getLogger(__name__).debug("事件处理异常", exc_info=True)

    def _dispatch(self, event: Event) -> None:
        # 1) 日志
        if event.message and event.kind not in ("progress",):
            level = event.level
            if event.kind == "file":
                level = "success"
            elif event.kind == "info":
                level = "info"
            self.log(event.message, level)

        # 2) 状态栏
        if event.kind in ("state", "login", "error", "info", "netdisk", "creds", "ready"):
            self._flash(event.message or "", event.level)
            self._refresh_header()

        if event.kind in ("state", "login", "ready"):
            self._refresh_header()

        # 3) 分发给各标签页
        for tab in self._tabs:
            try:
                tab.handle_event(event)
            except Exception:
                import logging

                logging.getLogger(__name__).debug(
                    "标签页 %s 处理事件失败", type(tab).__name__, exc_info=True
                )

        # 4) 严重错误给出「可操作」的提示（不刷屏）
        if event.kind == "error" and event.level == "error" and event.data.get("hint"):
            self._last_error = event

    def _flash(self, message: str, level: str = "info") -> None:
        if not message:
            return
        style = {
            "error": "Danger.TLabel",
            "warning": "Warn.TLabel",
            "success": "Ok.TLabel",
        }.get(level, "Muted.TLabel")
        try:
            self.status_text.configure(text=message[:150], style=style)
            self._status_reset_at = time.time()
        except tk.TclError:
            pass

    def _refresh_header(self) -> None:
        state = self.controller.monitor_state()
        label = {"running": "运行中", "paused": "已暂停", "stopped": "已停止"}.get(state, state)
        style = {"running": "Ok.TLabel", "paused": "Warn.TLabel"}.get(state, "Muted.TLabel")
        try:
            self.header_state.configure(text=label, style=style)
            data = self.controller.stats()
            self.header_detail.configure(
                text=f"{len(self.controller.config.groups)} 个群 · "
                     f"{data.get('seen', 0)} 个文件 · 已上传 {data.get('bytes_text', '0 B')}"
            )
        except tk.TclError:
            pass

    # ================================================================ 对外

    def log(self, message: str, level: str = "info") -> None:
        """把一行写到监控页的日志区（已脱敏）。"""
        for tab in self._tabs:
            if isinstance(tab, MonitorTab):
                tab.log_view.append(message, level, timestamp=time.strftime("%H:%M:%S"))
                break

    def save_all(self) -> list[str]:
        """把所有标签页的界面值写回配置并落盘。"""
        for tab in self._tabs:
            try:
                tab.on_save()
            except Exception as exc:
                self.log(f"保存 {tab.title} 设置时出错：{type(exc).__name__}", "error")
        try:
            return self.controller.save_settings()
        except QgbError as exc:
            self.log(exc.message, "error")
            return [exc.message]

    # ================================================================ 动作

    def _monitor(self, action: str) -> None:
        tab = next((t for t in self._tabs if isinstance(t, MonitorTab)), None)
        if tab is None:
            return
        self.notebook.select(tab)
        if action == "start":
            tab._start()
        elif action == "stop":
            tab._stop()
        elif action == "pause":
            tab._toggle_pause()
        elif action == "once":
            tab._run_once()

    def _on_tab_changed(self, _event=None) -> None:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        tab = self._tabs[index]
        try:
            tab.on_save()
            tab.refresh()
        except Exception:
            import logging

            logging.getLogger(__name__).debug("切换标签页刷新失败", exc_info=True)

    def _startup_checks(self) -> None:
        cfg = self.controller.config
        problems = cfg.validate()
        if not cfg.groups:
            self.log("尚未配置任何群号 —— 请到「群与规则」页添加。", "warning")
            try:
                self.notebook.select(1)
            except tk.TclError:
                pass
        elif problems:
            self.log("配置存在待解决项：" + "；".join(problems), "warning")
        else:
            self.log(f"配置就绪：{len(cfg.groups)} 个群待监控。", "success")

        if not self.controller.credentials_overview():
            self.log("尚未保存网盘凭据 —— 请到「网盘与凭据」页完成授权。", "warning")

        status = self.controller.napcat_status()
        if not status.get("installed"):
            self.log("尚未安装 QQ 组件（NapCat）—— 请到「QQ 登录」页按向导安装。",
                     "warning")
        elif not status.get("running"):
            self.log("QQ 组件已安装但未运行。", "muted")

        # OpenList（WebDAV 中转）是**独立进程**，忘了开就只会在「测试连接」里
        # 显示一句"连接失败"，极难联想到根因。所以启动时按需把它拉起来。
        # 只在 WebDAV 目标指向本机时才会真的动手（见 OpenListManager.is_local_target），
        # 将来把 OpenList 放到云服务器上会自动跳过。
        if cfg.upload.manage_openlist and cfg.upload.adapter == "webdav":
            self.controller.ensure_openlist(silent=True)

    def _show_health(self) -> None:
        try:
            data = self.controller.health()
        except Exception as exc:
            messagebox.showerror("自检失败", str(exc))
            return

        problems = data.get("problems") or []
        napcat = data.get("napcat", {})
        creds = data.get("credentials", {})
        text = (
            f"配置校验：{'✅ 通过' if not problems else '⚠ ' + '；'.join(problems)}\n"
            f"已配置群数：{data.get('groups')}\n"
            f"上传方式：{data.get('adapter')} → {data.get('target')}\n"
            f"凭据后端：{creds.get('backend')}\n"
            f"凭据条数：{creds.get('count')}\n"
            f"凭据文件：{creds.get('path')}\n"
            f"QQ 组件：{'运行中' if napcat.get('running') else ('已安装未运行' if napcat.get('installed') else '未安装')}\n"
            f"监控状态：{data.get('monitor')}\n"
            f"状态库：{data.get('state_db')}"
        )
        messagebox.showinfo("运行环境自检", text)

    def _show_about(self) -> None:
        messagebox.showinfo(
            f"关于 {APP_NAME}",
            f"{APP_NAME}  v{__version__}\n\n"
            "把指定 QQ 群的新文件自动抓取、过滤并上传到网盘。\n\n"
            "· 凭据以 Windows DPAPI 加密，绑定本机当前用户\n"
            "· 日志自动脱敏，不记录令牌/账号信息\n"
            "· 不向任何第三方服务器上报数据\n"
            "· 分发包内不含任何凭据\n\n"
            "仅供在知情同意的自有/授权设备上使用。",
        )

    # ================================================================ 关闭

    def _on_close(self) -> None:
        if self.controller.monitor_state() != "stopped":
            if not messagebox.askyesno(
                "确认退出",
                "监控正在运行，退出会停止搬运（已上传的去重记录会保留）。\n\n确定要退出吗？",
            ):
                return

        try:
            self.save_all()
        except Exception:
            pass

        try:
            self.controller.shutdown()
        except Exception:
            pass

        self.destroy()


def main() -> int:
    """GUI 入口。返回进程退出码。"""
    try:
        window = MainWindow()
    except Exception as exc:  # 起不来也要给出可读信息
        import traceback

        message = f"程序启动失败：{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}"
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动失败", message)
            root.destroy()
        except Exception:
            print(message, file=sys.stderr)
        return 1

    window.mainloop()
    return 0
