"""标签页基类与滚动容器。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..controller import Event
from .theme import Palette

__all__ = ["Tab", "ScrollFrame"]


class ScrollFrame(ttk.Frame):
    """可纵向滚动的容器（内容多时才显示滚动条）。"""

    def __init__(self, master, **kw) -> None:
        super().__init__(master, **kw)

        self.canvas = tk.Canvas(self, bg=Palette.BG, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll_set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.inner = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        # 鼠标进入才绑定滚轮，避免抢别的控件的滚轮事件
        self.canvas.bind("<Enter>", lambda _e: self._bind_wheel())
        self.canvas.bind("<Leave>", lambda _e: self._unbind_wheel())

    def _on_scroll_set(self, first: str, last: str) -> None:
        # 内容没超出时不显示滚动条
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scroll.grid_remove()
        else:
            self.scroll.grid()
        self.scroll.set(first, last)

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_wheel(self) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class Tab(ttk.Frame):
    """标签页基类。

    约定：
      * ``build()`` 只搭界面，不做 IO
      * ``refresh()`` 从 controller 拉状态刷新界面
      * ``handle_event()`` 处理后台事件（在工作线程产生的状态变化）
      * ``on_save()`` 把界面值写回 controller.config
    """

    title = "标签页"

    def __init__(self, master, controller, *, scale: float = 1.0, app=None) -> None:
        super().__init__(master, padding=(16, 14))
        self.controller = controller
        self.scale = scale
        self.app = app
        self.build()

    # -------------------------------------------------- 需要子类实现

    def build(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        """界面首次显示 / 配置变更后调用。"""

    def handle_event(self, event: Event) -> None:
        """处理后台事件。默认忽略。"""

    def on_save(self) -> None:
        """把界面控件的值写回 ``controller.config``（不落盘）。"""

    # -------------------------------------------------- 便捷方法

    def toast(self, message: str, level: str = "info") -> None:
        if self.app is not None:
            self.app.log(message, level)

    def set_busy(self, widget: ttk.Widget, busy: bool, text_idle: str = "") -> None:
        """统一的按钮忙碌态处理（禁用 + 改文案）。"""
        try:
            if busy:
                if text_idle:
                    widget.configure(text="处理中…")
                widget.state(["disabled"])
            else:
                if text_idle:
                    widget.configure(text=text_idle)
                widget.state(["!disabled"])
        except tk.TclError:
            pass
