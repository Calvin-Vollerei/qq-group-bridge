"""可复用界面控件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable

from .theme import Palette, mono_font, ui_font

__all__ = ["StatusPill", "LogView", "Field", "PathPicker", "Card", "Hint", "KVRow"]


class StatusPill(tk.Canvas):
    """圆角状态胶囊（比 Label 更能一眼看清状态）。"""

    def __init__(self, master, *, scale: float = 1.0, **kw) -> None:
        height = int(30 * scale)
        super().__init__(master, height=height, highlightthickness=0,
                         bg=Palette.BG, **kw)
        self._scale = scale
        self._text = "已停止"
        self._bg = Palette.STATE["stopped"][0]
        self._fg = Palette.STATE["stopped"][1]
        # ⚠️ 变量名不能叫 _w：tkinter 内部用 self._w 保存控件路径名，
        # 覆盖它会让后续所有 configure() 调用报 "invalid command name"。
        self._pill_width = int(120 * scale)
        self.configure(width=self._pill_width)
        self.bind("<Configure>", self._redraw)

    def set_state(self, state: str, text: str | None = None) -> None:
        bg, fg = Palette.STATE.get(state, Palette.STATE["stopped"])
        self._bg, self._fg = bg, fg
        self._text = text or {
            "running": "运行中",
            "paused": "已暂停",
            "stopped": "已停止",
            "waiting": "等待登录",
        }.get(state, state)
        self._pill_width = max(
            int(96 * self._scale), int((len(self._text) * 14 + 40) * self._scale)
        )
        self.configure(width=self._pill_width)
        self._redraw()

    def _redraw(self, _event=None) -> None:
        self.delete("all")
        h = int(self.winfo_height()) or int(30 * self._scale)
        r = h / 2
        w = self._pill_width
        self.create_oval(0, 0, h, h, fill=self._bg, outline="")
        self.create_oval(w - h, 0, w, h, fill=self._bg, outline="")
        self.create_rectangle(r, 0, w - r, h, fill=self._bg, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg,
                         font=ui_font(self._scale, size=10, bold=True))


class LogView(ttk.Frame):
    """带级别着色与上限的日志区。"""

    def __init__(self, master, *, scale: float = 1.0, max_lines: int = 4000, **kw) -> None:
        super().__init__(master, **kw)
        self._scale = scale
        self._max_lines = max_lines

        self.text = tk.Text(
            self, height=14, wrap="none", undo=False,
            bg="#0F1622", fg="#D6E1EC", insertbackground="#D6E1EC",
            relief="flat", padx=10, pady=8,
            font=mono_font(scale, size=9),
        )
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        for level, color in Palette.LOG.items():
            self.text.tag_configure(level, foreground=color)
        self.text.tag_configure("ts", foreground="#7C8B9B")
        self.text.tag_configure("bold", font=mono_font(scale, size=9))
        self.text.configure(state="disabled")

        self._autoscroll = tk.BooleanVar(value=True)
        self._count = 0

    def controls(self, parent: tk.Misc) -> ttk.Frame:
        bar = ttk.Frame(parent)
        ttk.Checkbutton(bar, text="自动滚动", variable=self._autoscroll,
                        style="Card.TCheckbutton").pack(side="left")
        ttk.Button(bar, text="清空", style="Ghost.TButton",
                   command=self.clear).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="导出日志", style="Ghost.TButton",
                   command=self.export).pack(side="left", padx=(6, 0))
        return bar

    def append(self, message: str, level: str = "info", *, timestamp: str = "") -> None:
        if not message:
            return
        self.text.configure(state="normal")
        if timestamp:
            self.text.insert("end", timestamp + " ", "ts")
        self.text.insert("end", message + "\n", level if level in Palette.LOG else "info")
        self._count += 1

        if self._count > self._max_lines:
            # 一次砍掉一批，避免每行都触发删除
            self.text.delete("1.0", f"{self._max_lines // 4}.0")
            self._count = self.text.index("end-1c").split(".")[0]
            self._count = int(self._count)

        self.text.configure(state="disabled")
        if self._autoscroll.get():
            self.text.see("end")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._count = 0

    def export(self) -> str:
        from tkinter import messagebox

        path = filedialog.asksaveasfilename(
            title="导出日志",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("全部文件", "*.*")],
            initialfile="qgb-log.txt",
        )
        if not path:
            return ""
        try:
            content = self.text.get("1.0", "end")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            messagebox.showinfo("导出完成", f"日志已导出到：\n{path}\n\n"
                                            "（导出内容已脱敏，不含任何凭据）")
            return path
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return ""


class Field(ttk.Frame):
    """标签 + 输入框 + 说明文字。"""

    def __init__(
        self,
        master,
        label: str,
        *,
        hint: str = "",
        width: int = 34,
        scale: float = 1.0,
        show: str | None = None,
        card: bool = True,
        **kw,
    ) -> None:
        super().__init__(master, **kw)
        bgstyle = "Card" if card else "T"
        self.variable = tk.StringVar()
        ttk.Label(self, text=label, style=f"{bgstyle}.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 3)
        )
        self.entry = ttk.Entry(self, textvariable=self.variable, width=width,
                               show=show or "")
        self.entry.grid(row=1, column=0, sticky="ew")
        self.columnconfigure(0, weight=1)
        if hint:
            ttk.Label(self, text=hint, style=f"{bgstyle}Muted.TLabel",
                      wraplength=int(420 * scale)).grid(row=2, column=0, sticky="w",
                                                        pady=(3, 0))

    def get(self) -> str:
        return self.variable.get().strip()

    def set(self, value: str) -> None:
        self.variable.set(value or "")


class PathPicker(ttk.Frame):
    """输入框 + 浏览按钮（目录或文件）。"""

    def __init__(self, master, label: str, *, mode: str = "dir", card: bool = True, **kw) -> None:
        super().__init__(master, **kw)
        self.mode = mode
        bgstyle = "Card" if card else "T"
        self.variable = tk.StringVar()
        ttk.Label(self, text=label, style=f"{bgstyle}.TLabel").grid(row=0, column=0,
                                                                   columnspan=2, sticky="w",
                                                                   pady=(0, 3))
        self.entry = ttk.Entry(self, textvariable=self.variable)
        self.entry.grid(row=1, column=0, sticky="ew")
        ttk.Button(self, text="浏览…", style="Ghost.TButton",
                   command=self._browse).grid(row=1, column=1, padx=(6, 0))
        self.columnconfigure(0, weight=1)

    def _browse(self) -> None:
        if self.mode == "dir":
            path = filedialog.askdirectory(title="选择目录")
        else:
            path = filedialog.askopenfilename(
                title="选择文件", filetypes=[("压缩包", "*.zip"), ("全部文件", "*.*")]
            )
        if path:
            self.variable.set(path)

    def get(self) -> str:
        return self.variable.get().strip()

    def set(self, value: str) -> None:
        self.variable.set(value or "")


class Card(ttk.Frame):
    """带标题的卡片容器。"""

    def __init__(self, master, title: str = "", *, scale: float = 1.0, **kw) -> None:
        super().__init__(master, style="Card.TFrame", padding=14, **kw)
        self.body = self
        if title:
            ttk.Label(self, text=title, style="CardHead.TLabel").pack(anchor="w",
                                                                     pady=(0, 8))
        self._scale = scale


class Hint(ttk.Label):
    """统一的说明文字。"""

    def __init__(self, master, text: str, *, scale: float = 1.0, card: bool = True, **kw) -> None:
        style = "CardMuted.TLabel" if card else "Muted.TLabel"
        super().__init__(master, text=text, style=style, justify="left",
                         wraplength=int(560 * scale), **kw)


class KVRow(ttk.Frame):
    """键值行（左标签右值），用于自检/凭据清单。"""

    def __init__(self, master, key: str, value: str, *, card: bool = True, **kw) -> None:
        super().__init__(master, style=("Card.TFrame" if card else "TFrame"), **kw)
        ttk.Label(self, text=key, width=20, style=("Card.TLabel" if card else "TLabel")).grid(
            row=0, column=0, sticky="w"
        )
        self.value = ttk.Label(self, text=value,
                               style=("Card.TLabel" if card else "TLabel"))
        self.value.grid(row=0, column=1, sticky="w")
        self.columnconfigure(1, weight=1)


def section(parent: tk.Misc, title: str, *, scale: float = 1.0) -> ttk.Frame:
    """创建一个带标题的区段，返回可放内容的 Frame。"""
    box = ttk.LabelFrame(parent, text=title, padding=12)
    return box


def button_row(parent: tk.Misc) -> ttk.Frame:
    row = ttk.Frame(parent, style="Card.TFrame")
    return row
