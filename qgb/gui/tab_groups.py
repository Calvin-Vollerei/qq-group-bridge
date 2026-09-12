"""群与规则页：群号清单、正则过滤、体积限制、规则试跑。"""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk

from ..config import RE_FILTER_TEMPLATES
from ..controller import Event
from ..naming import (
    FOLDER_STYLES,
    format_folder_map,
    has_folder_conflicts,
    parse_folder_map,
    resolve_group_folder,
    sanitize_folder,
)
from ..filters import CompiledFilters
from .base import ScrollFrame, Tab
from .theme import Palette, mono_font, ui_font
from .widgets import Card, Field, Hint

__all__ = ["GroupsTab"]


class GroupsTab(Tab):
    title = "群与规则"

    def build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        scroller = ScrollFrame(self)
        scroller.grid(row=0, column=0, sticky="nsew")
        body = scroller.inner
        body.columnconfigure(0, weight=1)

        # ---------- 群号
        groups = Card(body, scale=self.scale)
        groups.grid(row=0, column=0, sticky="ew")
        groups.columnconfigure(0, weight=1)

        ttk.Label(groups, text="要监控的 QQ 群", style="CardHead.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.groups_text = tk.Text(groups, height=6, wrap="none", relief="flat",
                                   bg="#FFFFFF", fg=Palette.TEXT,
                                   font=mono_font(self.scale, size=10), padx=8, pady=6)
        self.groups_text.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        Hint(groups, "每行一个群号。需要部署方的 QQ 号已加入这些群。",
             scale=self.scale).grid(row=2, column=0, sticky="w", pady=(6, 0))

        # ---------- 网盘目录名
        naming = Card(body, scale=self.scale)
        naming.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        naming.columnconfigure(0, weight=1)

        ttk.Label(naming, text="网盘里的目录名（可选）",
                  style="CardHead.TLabel").grid(row=0, column=0, sticky="w")

        self.folder_text = tk.Text(naming, height=5, wrap="none", relief="flat",
                                   bg="#FFFFFF", fg=Palette.TEXT,
                                   font=mono_font(self.scale, size=10), padx=8, pady=6)
        self.folder_text.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.folder_hint = ttk.Label(naming, text="", style="CardMuted.TLabel",
                                     justify="left", wraplength=int(660 * self.scale))
        self.folder_hint.grid(row=2, column=0, sticky="w", pady=(6, 0))

        ttk.Button(naming, text="📋  按群名一键生成", style="Ghost.TButton",
                   command=self._fill_from_group_names).grid(
            row=3, column=0, sticky="w", pady=(8, 0))

        Hint(naming,
             "每行写「群号 = 目录名」，例如：123456789 = 示例学习群。"
             "留空的群按「网盘与凭据」页里的「群目录命名」规则自动命名（默认用群号）。",
             scale=self.scale).grid(row=4, column=0, sticky="w", pady=(6, 0))

        # ---------- 文件名规则
        rules = Card(body, scale=self.scale)
        rules.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        rules.columnconfigure(0, weight=1)
        rules.columnconfigure(1, weight=1)

        ttk.Label(rules, text="文件名过滤（正则）", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )

        ttk.Label(rules, text="包含（命中任意一条即通过）", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", pady=(8, 3)
        )
        self.include_text = tk.Text(rules, height=5, wrap="none", relief="flat",
                                    bg="#FFFFFF", fg=Palette.TEXT,
                                    font=mono_font(self.scale, size=10), padx=8, pady=6)
        self.include_text.grid(row=2, column=0, sticky="nsew", padx=(0, 10))

        ttk.Label(rules, text="排除（命中任意一条即丢弃，优先级更高）",
                  style="Card.TLabel").grid(row=1, column=1, sticky="w", pady=(8, 3))
        self.exclude_text = tk.Text(rules, height=5, wrap="none", relief="flat",
                                    bg="#FFFFFF", fg=Palette.TEXT,
                                    font=mono_font(self.scale, size=10), padx=8, pady=6)
        self.exclude_text.grid(row=2, column=1, sticky="nsew")

        tpl_row = ttk.Frame(rules, style="Card.TFrame")
        tpl_row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(tpl_row, text="常用模板：", style="Card.TLabel").pack(side="left")
        self.template = ttk.Combobox(tpl_row, state="readonly", width=30,
                                     values=[name for name, _ in RE_FILTER_TEMPLATES])
        self.template.pack(side="left")
        ttk.Button(tpl_row, text="插入到「包含」", style="Ghost.TButton",
                   command=self._insert_template).pack(side="left", padx=(8, 0))
        ttk.Button(tpl_row, text="🔍  规则试跑", style="Ghost.TButton",
                   command=self._selftest).pack(side="left", padx=(8, 0))

        Hint(rules,
             "无需写 ^$ 全匹配：规则按「搜索」语义生效。"
             "建议同时设置排除规则（如 ~$ 开头的 Office 临时文件），避免搬入垃圾文件。",
             scale=self.scale).grid(row=4, column=0, columnspan=2, sticky="w",
                                    pady=(8, 0))

        # ---------- 类型与体积
        limits = Card(body, scale=self.scale)
        limits.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        limits.columnconfigure(0, weight=1)
        limits.columnconfigure(1, weight=1)
        limits.columnconfigure(2, weight=1)

        ttk.Label(limits, text="类型与体积限制", style="CardHead.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )

        self.f_ext = Field(limits, "扩展名白名单（逗号分隔）", scale=self.scale,
                           hint="留空表示不限制。例如：xlsx, pdf", width=22)
        self.f_ext.grid(row=1, column=0, sticky="ew", padx=(0, 10))

        self.f_min = Field(limits, "最小体积（MB）", scale=self.scale,
                           hint="0 表示不限", width=14)
        self.f_min.grid(row=1, column=1, sticky="ew", padx=(0, 10))

        self.f_max = Field(limits, "最大体积（MB）", scale=self.scale,
                           hint="0 表示不限（仍受「高级」里的单文件上限约束）", width=14)
        self.f_max.grid(row=1, column=2, sticky="ew")

        # ---------- 试跑结果
        result = Card(body, scale=self.scale)
        result.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        result.columnconfigure(0, weight=1)

        ttk.Label(result, text="规则试跑结果", style="CardHead.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        self.tree = ttk.Treeview(result, columns=("name", "size", "result", "reason"),
                                 show="headings", height=7)
        for col, text, width in (
            ("name", "示例文件名", 240),
            ("size", "大小(MB)", 80),
            ("result", "结果", 70),
            ("reason", "原因", 420),
        ):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="ew")
        self.tree.tag_configure("ok", foreground="#1E7A5F")
        self.tree.tag_configure("no", foreground=Palette.MUTED)

        self.refresh()

    # -------------------------------------------------- 刷新

    def refresh(self) -> None:
        cfg = self.controller.config

        if not self.groups_text.get("1.0", "end").strip():
            self.groups_text.insert("1.0", "\n".join(cfg.groups))

        if not self.include_text.get("1.0", "end").strip():
            self.include_text.insert("1.0", "\n".join(cfg.filters.include))
        if not self.exclude_text.get("1.0", "end").strip() and cfg.filters.exclude:
            self.exclude_text.insert("1.0", "\n".join(cfg.filters.exclude))

        self.f_ext.set(", ".join(cfg.filters.extensions))
        self.f_min.set(str(cfg.filters.min_size_mb or 0))
        self.f_max.set(str(cfg.filters.max_size_mb or 0))

        if not self.folder_text.get("1.0", "end").strip() and cfg.group_folder_names:
            self.folder_text.insert("1.0", format_folder_map(cfg.group_folder_names))
        self._update_folder_hint()

    def _update_folder_hint(self) -> None:
        """把「实际会用到哪些目录名」直接显示出来 —— 比让用户脑补规则强。"""
        cfg = self.controller.config
        mapping = parse_folder_map(self.folder_text.get("1.0", "end"))
        style = cfg.upload.folder_style
        style_label = FOLDER_STYLES.get(style, style)

        if not cfg.groups:
            self.folder_hint.configure(text="（还没填群号）")
            return

        names = self.controller.cached_group_names()
        preview: list[str] = []
        for gid in cfg.groups[:6]:
            folder = resolve_group_folder(
                gid,
                custom=mapping.get(gid, ""),
                style=style,
                group_name=names.get(gid, ""),
            )
            preview.append(f"{gid} → {folder or '(无)'}")

        lines = [f"自动命名规则：{style_label}（自定义的优先）", "预览：" + "； ".join(preview)]
        if len(cfg.groups) > 6:
            lines.append(f"…另有 {len(cfg.groups) - 6} 个群")

        conflicts = has_folder_conflicts(mapping, cfg.groups)
        if conflicts:
            lines.append("⚠ 多个群会写进同一个目录：" + "；".join(conflicts))

        if style in ("name", "id_name") and not names:
            lines.append("提示：群名还没读到，暂时用群号；点「按群名一键生成」会去拉取。")

        self.folder_hint.configure(text="\n".join(lines))

    def _fill_from_group_names(self) -> None:
        """去 OneBot 拉群名，然后按「群号 = 群名」填进去。"""
        if self.controller.load_group_names():
            self.toast("正在读取群名…")

    def apply_group_names(self, names: dict) -> None:
        """收到群名后填充文本框（保留用户已填的行）。"""
        cfg = self.controller.config
        existing = parse_folder_map(self.folder_text.get("1.0", "end"))
        for gid in cfg.groups:
            if not existing.get(gid) and names.get(gid):
                existing[gid] = sanitize_folder(names[gid], fallback=gid)
        self.folder_text.delete("1.0", "end")
        self.folder_text.insert("1.0", format_folder_map(existing))
        self._update_folder_hint()
        self.toast(f"已按群名填入 {len(existing)} 条目录名", "success")

    # -------------------------------------------------- 保存

    def on_save(self) -> None:
        cfg = self.controller.config

        raw_groups = self.groups_text.get("1.0", "end")
        groups: list[str] = []
        for line in raw_groups.splitlines():
            value = line.strip().replace(",", " ").replace("，", " ")
            for token in value.split():
                token = re.sub(r"\D", "", token)
                if token:
                    groups.append(token)
        cfg.groups = groups

        # 网盘目录名映射：解析「群号 = 目录名」多行文本。
        # 只保留仍被监控的群，避免配置越积越多。
        mapping = parse_folder_map(self.folder_text.get("1.0", "end"))
        cfg.group_folder_names = {
            gid: name for gid, name in mapping.items() if gid in groups
        }
        self._update_folder_hint()

        def lines(widget: tk.Text) -> list[str]:
            out = []
            for line in widget.get("1.0", "end").splitlines():
                value = line.strip()
                if value and not value.startswith("#"):
                    out.append(value)
            return out

        cfg.filters.include = lines(self.include_text) or [r".*"]
        cfg.filters.exclude = lines(self.exclude_text)
        cfg.filters.extensions = [
            e.strip().lower().lstrip(".")
            for e in self.f_ext.get().replace("，", ",").split(",")
            if e.strip()
        ]

        def num(field: Field, default: float = 0.0) -> float:
            try:
                return max(0.0, float(field.get() or default))
            except ValueError:
                return default

        cfg.filters.min_size_mb = num(self.f_min)
        cfg.filters.max_size_mb = num(self.f_max)

    # -------------------------------------------------- 动作

    def _insert_template(self) -> None:
        index = self.template.current()
        if index < 0:
            return
        _, pattern = RE_FILTER_TEMPLATES[index]
        current = self.include_text.get("1.0", "end").strip()
        if current in ("", r".*"):
            self.include_text.delete("1.0", "end")
        else:
            self.include_text.insert("end", "\n")
        self.include_text.insert("end", pattern)

    def _selftest(self) -> None:
        self.on_save()
        try:
            compiled = CompiledFilters(self.controller.config.filters)
        except Exception as exc:
            self.toast(f"规则无效：{exc}", "error")
            return

        for item in self.tree.get_children():
            self.tree.delete(item)

        results = compiled.selftest()
        for row in results:
            self.tree.insert(
                "",
                "end",
                values=(row["name"], row["size_mb"], "通过" if row["accepted"] else "跳过",
                        row["reason"]),
                tags=("ok" if row["accepted"] else "no",),
            )
        accepted = sum(1 for r in results if r["accepted"])
        self.toast(f"规则试跑完成：{accepted}/{len(results)} 个示例通过", "success")

    def handle_event(self, event: Event) -> None:
        # 「按群名一键生成」拿到群名后回填目录名
        if event.kind == "group_names":
            names = event.data.get("names") or {}
            if names:
                self.apply_group_names(names)
            else:
                self.toast(event.message or "读取群名失败", "warning")
