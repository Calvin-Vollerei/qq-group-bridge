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
        # 文件列表弹窗的筛选/排序状态：**放在 Tab 上而不是弹窗里** ——
        # 否则每次关闭再打开都回到默认值（用户实测反馈"一关窗口就重置了"）。
        self._flt_search = tk.StringVar(master=self, value="")
        self._flt_status = tk.StringVar(master=self, value="全部")
        self._flt_group = tk.StringVar(master=self, value="全部")
        self._flt_size = tk.StringVar(master=self, value="全部")
        self._flt_sort = tk.StringVar(master=self, value="queue")
        self._flt_desc = tk.BooleanVar(master=self, value=False)

        self.btn_once = ttk.Button(mini, text="🔄  立即刷新（拉取新文件）",
                                   style="Accent.TButton", command=self._run_once)
        self.btn_once.pack(side="left")
        ttk.Button(mini, text="清空记录并重新发现", style="Ghost.TButton",
                   command=self._reset_discovery).pack(side="left", padx=(8, 0))
        ttk.Button(mini, text="重试失败项", style="Ghost.TButton",
                   command=self._requeue).pack(side="left", padx=(8, 0))
        ttk.Button(mini, text="📋  文件列表 / 下载顺序", style="Ghost.TButton",
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
        ("new_cycle", "本轮新发现"),
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
        self.boxes["new_cycle"].set(str(data.get("new_last_cycle", 0)))
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

    def _reset_discovery(self) -> None:
        """清空累计的待处理/过滤/失败记录，让下次扫描重新发现。

        界面上「待处理」是**累计**值：一次全量扫描会把群里所有文件都登记进来，
        之后即使文件已被删除或用户不想搬，记录也会一直留着，队列越积越多。
        """
        if not messagebox.askyesno(
            "清空记录并重新发现",
            "将清空「待处理 / 已过滤 / 失败」的累计记录，"
            "然后重新扫描群文件。\n\n"
            "· 已搬完的记录会保留（用于去重，不会重复上传）\n"
            "· 下次扫描会按当前群文件重新建立待办列表\n\n"
            "确定继续吗？",
        ):
            return
        try:
            result = self.controller.reset_discovery()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("清空失败", f"{type(exc).__name__}: {exc}")
            return
        self.toast(f"已清空 {result.get('removed', 0)} 条累计记录，"
                   "点「立即刷新」重新发现群文件", "success")
        self.refresh()

    def _requeue(self) -> None:
        n = self.controller.requeue_failed()
        self.toast(f"已把 {n} 个失败项重新排队", "success" if n else "muted")
        self._refresh_stats()

    def _show_records(self) -> None:
        """文件列表：排序 + 搜索 + 置顶/上移下移。

        设计取舍（按用户要求）：**一个表**，不分"待下载"和"已处理"两块 ——
        排序/搜索对全表生效；置顶与上下移只对「待处理」的行有意义
        （已搬完的文件没有队列位置），所以按钮会自动禁用并给出说明。

        排序在本地按当前数据做：与用户直觉一致，且不与队列的手工顺序打架
        （手工顺序只在"按队列顺序"这一档下体现）。
        """
        from ..utils import human_size

        win = tk.Toplevel(self)
        win.title("文件列表与下载顺序")
        # 自适应：按屏幕尺寸取，避免固定宽度下按钮被挤出可视区（用户实测反馈）
        # 自适应要点（用户反馈"拓展窗口不能自适应 Win 的多窗口"）：
        #   1. 以**主窗口当前所在显示器**的工作区为准，而不是主屏 ——
        #      多屏时 `winfo_screenwidth()` 只反映主屏，窗口会被放到别的屏外；
        #   2. 用 Tk 的 scaling 与屏幕尺寸取合理比例，并保证不小于最小可用尺寸；
        #   3. 允许自由拉伸，列宽靠 stretch 分配（文件名列占剩余空间）。
        win.update_idletasks()
        root = self.winfo_toplevel()
        scale = float(win.tk.call("tk", "scaling")) or 1.0

        # 主窗口位置 + 尺寸 → 推断所在显示器；取不到就退回整屏尺寸
        rx, ry = root.winfo_rootx(), root.winfo_rooty()
        rw, rh = root.winfo_width(), root.winfo_height()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        # 显示器近似区域：以主窗口为中心、整屏为界的裁切
        left = max(0, rx - rw // 2)
        top = max(0, ry - rh // 2)
        avail_w = max(800, sw - left)
        avail_h = max(520, sh - top)

        w = int(max(880 * scale / 1.33, min(avail_w * 0.95, 1500)))
        h = int(max(520 * scale / 1.33, min(avail_h * 0.88, 960)))
        x = left + max(0, (avail_w - w) // 2)
        y = top + max(0, (avail_h - h) // 3)
        win.geometry(f"{w}x{h}+{x}+{y}")
        win.minsize(860, 500)
        # 允许最大化与自由缩放；行/列都按窗口拉伸
        win.rowconfigure(0, weight=0)
        win.resizable(True, True)
        win.configure(bg=Palette.BG)
        win.transient(self.winfo_toplevel())

        state_label = {
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

        # ---------------- 顶部：排序 + 搜索
        top = ttk.Frame(win, padding=(12, 10, 12, 6))
        top.pack(fill="x")

        ttk.Label(top, text="排序：", style="CardMuted.TLabel").pack(side="left")

        sort_col = self._flt_sort
        sort_desc = self._flt_desc
        search_var = self._flt_search

        def on_sort() -> None:
            refresh()

        for value, text in (
            ("queue", "下载顺序"),
            ("name", "文件名"),
            ("group", "群号"),
            ("size", "大小"),
            ("time", "上传时间"),
            ("updated", "处理时间"),
        ):
            ttk.Radiobutton(top, text=text, value=value, variable=sort_col,
                            command=on_sort).pack(side="left", padx=(6, 0))

        ttk.Checkbutton(top, text="倒序", variable=sort_desc,
                        command=on_sort).pack(side="left", padx=(12, 0))

        ttk.Label(top, text="  搜索：", style="CardMuted.TLabel").pack(side="left")
        entry = ttk.Entry(top, textvariable=search_var, width=22)
        entry.pack(side="left")
        entry.bind("<KeyRelease>", lambda _e: refresh())
        ttk.Button(top, text="✕", width=3, style="Ghost.TButton",
                   command=lambda: (search_var.set(""), refresh())).pack(side="left", padx=(4, 0))

        # ---------------- 第二行：筛选（状态 / 群聊 / 大小）
        filt = ttk.Frame(win, padding=(12, 0, 12, 4))
        filt.pack(fill="x")

        status_var = self._flt_status
        group_var = self._flt_group
        size_var = self._flt_size

        ttk.Label(filt, text="筛选：状态", style="CardMuted.TLabel").pack(side="left")
        status_box = ttk.Combobox(filt, textvariable=status_var, width=10, state="readonly",
                                  values=("全部", "待处理", "下载中", "上传中", "已完成",
                                          "失败", "已过滤"))
        status_box.pack(side="left", padx=(4, 12))
        status_box.bind("<<ComboboxSelected>>", lambda _e: refresh())

        ttk.Label(filt, text="群聊", style="CardMuted.TLabel").pack(side="left")
        group_box = ttk.Combobox(filt, textvariable=group_var, width=14, state="readonly",
                                 values=("全部",))
        group_box.pack(side="left", padx=(4, 12))
        group_box.bind("<<ComboboxSelected>>", lambda _e: refresh())

        ttk.Label(filt, text="大小", style="CardMuted.TLabel").pack(side="left")
        size_box = ttk.Combobox(filt, textvariable=size_var, width=14, state="readonly",
                                values=("全部", "< 1 MB", "1–10 MB", "10–100 MB", "≥ 100 MB"))
        size_box.pack(side="left", padx=(4, 12))
        size_box.bind("<<ComboboxSelected>>", lambda _e: refresh())

        ttk.Button(filt, text="⤒ 优先执行筛选结果", style="Accent.TButton",
                   command=lambda: prioritize()).pack(side="left")
        ttk.Button(filt, text="清除筛选", style="Ghost.TButton",
                   command=lambda: (search_var.set(""), status_var.set("全部"),
                                    group_var.set("全部"), size_var.set("全部"),
                                    refresh())).pack(side="left", padx=(8, 0))

        ttk.Label(win,
                  text="在搜索框输入文件名/群号即可筛选。★置顶与 ↑↓ 只对「待处理」的文件生效"
                       "（已搬完的没有队列位置）；改完立即生效，监控不用重启。",
                  style="Muted.TLabel", padding=(12, 0, 12, 6)).pack(anchor="w")

        # ---------------- 表格
        wrap = ttk.Frame(win, padding=(12, 0, 12, 6))
        wrap.pack(fill="both", expand=True)

        columns = ("pin", "name", "group", "size", "uptime", "state", "updated")
        tree = ttk.Treeview(wrap, columns=columns, show="headings", height=18)
        # 列标题也支持排序：点一下按该列排（体验与"顶部按钮"等价）
        col_map = {
            "name": "name", "group": "group", "size": "size",
            "uptime": "time", "updated": "updated",
        }
        # 列宽：写死像素的旧做法在窄屏上会把后几列挤出去。
        # 这里给出**建议宽度 + 伸缩权重**：文件名占掉多余空间，其余列固定。
        for col, text, width, weight in (
            ("pin", "★", 34, 0),
            ("name", "文件名", 340, 1),        # 唯一可伸缩列
            ("group", "群号", 100, 0),
            ("size", "大小", 90, 0),
            ("uptime", "上传时间", 130, 0),
            ("state", "状态", 84, 0),
            ("updated", "处理时间", 130, 0),
        ):
            tree.heading(col, text=text,
                         command=(lambda c=col: (
                             sort_col.set(col_map.get(c, "queue")), on_sort()
                         )) if col in col_map else "")
            tree.column(col, width=width, minwidth=50,
                        stretch=bool(weight), anchor="w")

        scroll = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # ---------------- 底部：动作
        bottom = ttk.Frame(win, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")

        info = ttk.Label(bottom, text="", style="Muted.TLabel", justify="left")
        info.pack(side="left")

        def selected_key():
            sel = tree.selection()
            if not sel:
                return None, None
            vals = tree.item(sel[0], "values")
            raw = str(vals[-1])                      # 隐藏的 key 放在最后一项
            if not raw:
                return None, None
            g, b, f = raw.split("\x1f")
            return (g, int(b), f), sel[0]

        def size_match(row) -> bool:
            bucket = size_var.get()
            if bucket == "全部":
                return True
            mb = int(row.get("size") or 0) / 1048576
            if bucket == "< 1 MB":
                return mb < 1
            if bucket == "1–10 MB":
                return 1 <= mb < 10
            if bucket == "10–100 MB":
                return 10 <= mb < 100
            if bucket == "≥ 100 MB":
                return mb >= 100
            return True

        def refresh(keep: str | None = None) -> None:
            rows = self.controller.list_files(search=search_var.get())

            # 群聊下拉：按当前数据自动填充（不写死，避免与配置里的群号脱节）
            groups = sorted({str(r.get("group_id", "")) for r in rows if r.get("group_id")})
            want = ["全部"] + [_mask_group(g) for g in groups]
            if list(group_box["values"]) != want:
                group_box["values"] = want

            want_status = status_var.get()
            want_group = group_var.get()
            if want_status != "全部":
                rows = [r for r in rows
                        if state_label.get(str(r.get("state", "")), "") == want_status]
            if want_group != "全部":
                rows = [r for r in rows
                        if _mask_group(str(r.get("group_id", ""))) == want_group]
            rows = [r for r in rows if size_match(r)]
            col = sort_col.get()
            desc = bool(sort_desc.get())

            def name_key(r):
                return str(r.get("name", "")).lower()

            def size_key(r):
                return int(r.get("size") or 0)

            def uptime_key(r):
                return float(r.get("upload_time") or 0)

            def updated_key(r):
                return float(r.get("updated_at") or 0)

            def group_key(r):
                return str(r.get("group_id", ""))

            if col == "name":
                rows.sort(key=name_key, reverse=desc)
            elif col == "size":
                rows.sort(key=size_key, reverse=desc)
            elif col == "time":
                rows.sort(key=uptime_key, reverse=desc)
            elif col == "updated":
                rows.sort(key=updated_key, reverse=desc)
            elif col == "group":
                rows.sort(key=group_key, reverse=desc)
            # col == "queue"：保持 controller 返回的队列顺序

            tree.delete(*tree.get_children())
            for r in rows:
                pending = r.get("state") == TransferState.DISCOVERED.value
                star = "★" if r.get("pinned") else ("·" if pending else "")
                key = f"{r['group_id']}\x1f{r['busid']}\x1f{r['file_id']}"
                item = tree.insert("", "end", values=(
                    star,
                    r.get("name", ""),
                    _mask_group(str(r.get("group_id", ""))),
                    human_size(r.get("size", 0)),
                    time.strftime("%m-%d %H:%M", time.localtime(r.get("upload_time") or 0))
                    if r.get("upload_time") else "—",
                    state_label.get(str(r.get("state", "")), str(r.get("state", ""))),
                    time.strftime("%m-%d %H:%M", time.localtime(r.get("updated_at") or 0)),
                    str(r.get("state", "")),   # 隐藏：原始状态值（判断一律用它）
                    key,                       # 隐藏：记录 key
                ))
                if keep and key == keep:
                    tree.selection_set(item)
                    tree.see(item)
            pending_n = sum(1 for r in rows if r.get("state") == TransferState.DISCOVERED.value)
            base = self.controller.stats()
            seen_total = int(base.get("seen") or 0)
            counts = base.get("counts") or {}
            pend_total = int(counts.get("discovered") or 0)
            done_total = int(counts.get("done") or 0) + int(counts.get("uploaded") or 0)
            new_cycle = int(base.get("new_last_cycle") or 0)
            info.configure(
                text=(f"本表 {len(rows)} 项（其中待处理 {pending_n} 项）　|　"
                      f"累计：已见 {seen_total}、待处理 {pend_total}、已搬完 {done_total}"
                      f"　|　本轮新发现 {new_cycle} 个")
            )

        def prioritize() -> None:
            """把**当前筛选结果里的待处理项**按显示顺序提到队首。

            置顶项保持最前不动；已是待处理以外的状态（已完成/失败等）会自动跳过 ——
            它们没有队列位置。搬完一批后可以再筛再优先，实现"分批推进"。
            """
            keys: list[tuple[str, int, str]] = []
            for item in tree.get_children():
                vals = tree.item(item, "values")
                raw = str(vals[-1])
                if not raw:
                    continue
                g, b, f = raw.split("\x1f")
                # ⚠️ 必须比对**原始状态值**（-2 位）。踩过的坑：列里存的是中文
                # 标签"待处理"，再拿它去查 state_label（键是 "discovered"）永远
                # 查不到 → 永远判定"没有待处理"，按钮形同虚设。
                if str(vals[-2]) != TransferState.DISCOVERED.value:
                    continue
                keys.append((g, int(b), f))
            if not keys:
                self.toast("当前筛选结果里没有「待处理」的文件", "info")
                return
            moved = self.controller.prioritize_filtered(keys)
            self.toast(f"已把 {moved} 个文件排到队列前面，会优先搬运", "success")
            refresh()

        def act(fn) -> None:
            key, item = selected_key()
            if key is None:
                self.toast("请先选中一行", "warning")
                return
            row = next((r for r in self.controller.list_files(search=search_var.get())
                        if (str(r["group_id"]), int(r["busid"]), str(r["file_id"])) == key), None)
            if row is not None and row.get("state") != TransferState.DISCOVERED.value:
                label = state_label.get(str(row.get("state", "")), str(row.get("state", "")))
                self.toast(f"这一项状态是「{label}」，不是「待处理」，没有队列位置。"
                           "失败项请先点「重试失败项」把它变回待处理。", "info")
                return
            try:
                changed = fn(key)
            except Exception as exc:  # noqa: BLE001 - 界面动作不许把窗口带崩
                self.toast(f"操作失败：{type(exc).__name__}: {exc}", "error")
                return
            if not changed:
                # 说清"到哪条边界"，别再让用户猜（原来只有一句笼统的"已经到边界了"）
                queue_keys = {f"{r['group_id']}\x1f{r['busid']}\x1f{r['file_id']}"
                              for r in self.controller.queue_ordered()}
                if f"{key[0]}\x1f{key[1]}\x1f{key[2]}" not in queue_keys:
                    self.toast("这一项不在待处理队列里（可能刚被搬走或状态已变），"
                               "点「🔄 刷新」后重试", "warning")
                else:
                    self.toast("已经在队首/队尾，或不能跨越置顶边界", "info")
            refresh(keep=f"{key[0]}\x1f{key[1]}\x1f{key[2]}")

        ttk.Button(bottom, text="★ 置顶", style="Accent.TButton",
                   command=lambda: act(lambda k: self.controller.queue_pin(k, True))
                   ).pack(side="right")
        ttk.Button(bottom, text="取消置顶", style="Ghost.TButton",
                   command=lambda: act(lambda k: self.controller.queue_pin(k, False))
                   ).pack(side="right", padx=(0, 8))
        ttk.Button(bottom, text="↑ 上移", style="Ghost.TButton",
                   command=lambda: act(lambda k: self.controller.queue_move(k, -1))
                   ).pack(side="right", padx=(0, 8))
        ttk.Button(bottom, text="↓ 下移", style="Ghost.TButton",
                   command=lambda: act(lambda k: self.controller.queue_move(k, +1))
                   ).pack(side="right", padx=(0, 8))
        # 「刷新」放在**左侧**、紧跟信息标签：原先它和另外四个按钮都 right 对齐，
        # 一排按钮宽度超出窗口时它会被挤出可视区 —— 用户反馈"刷新按钮没了"。
        ttk.Button(bottom, text="🔄 刷新列表", style="Ghost.TButton",
                   command=lambda: refresh()).pack(side="left", padx=(10, 0))

        tree.bind("<Double-1>", lambda _e: act(lambda k: self.controller.queue_pin(k, True)))
        refresh()

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
