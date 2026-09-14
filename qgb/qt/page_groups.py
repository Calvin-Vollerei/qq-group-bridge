"""群与规则页：群号、网盘目录名、筛选规则 + 规则试跑。

补齐旧 Tk 版功能（对照 qgb/gui/tab_groups.py）：
  要监控的 QQ 群 / 按群名一键生成目录名 / 文件名过滤（包含·排除，正则）
  常用模板 / 规则试跑（样例文件看命中结果）/ 类型与体积限制（扩展名·最小·最大）
"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..filters import CompiledFilters
from ..naming import has_folder_conflicts, parse_folder_map, sanitize_folder
from .shell import Page
from .widgets import Card, Hint, SectionTitle

log = logging.getLogger(__name__)

#: 常用正则模板（覆盖最常见的「只搬某几类文件」诉求）
TEMPLATES: list[tuple[str, str]] = [
    ("只搬 PDF 与 Office 文档", r".*\.(pdf|docx?|xlsx?|pptx?)$"),
    ("只搬 PDF", r".*\.pdf$"),
    ("只要压缩包", r".*\.(zip|rar|7z)$"),
    ("排除临时/隐藏文件", r".*(~\$|thumbs\.db|desktop\.ini).*"),
    ("排除图片", r".*\.(png|jpe?g|gif|bmp|webp)$"),
    ("排除视频", r".*\.(mp4|mkv|avi|mov|flv)$"),
]


class GroupsPage(Page):
    title = "群与规则"

    def build(self) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget, QScrollArea > QWidget "
            "{ background: transparent; border: none; }"
        )
        area.viewport().setAutoFillBackground(False)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        # ---------------- 群号
        groups = Card()
        head = groups.row()
        head.addWidget(SectionTitle("要监控的 QQ 群"))
        head.addStretch(1)
        self.group_count = Hint("")
        head.addWidget(self.group_count)

        self.group_list = QListWidget()
        self.group_list.setMinimumHeight(120)
        groups.add(self.group_list)

        add_row = groups.row()
        self.new_group = QLineEdit()
        self.new_group.setPlaceholderText("输入群号，例如 123456789（回车也可添加）")
        self.new_group.returnPressed.connect(self._add_group)
        add_row.addWidget(self.new_group, 1)
        btn_add = QPushButton("添加")
        btn_add.setObjectName("Primary")
        btn_add.clicked.connect(self._add_group)
        add_row.addWidget(btn_add)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._remove_group)
        add_row.addWidget(btn_del)
        groups.add(Hint("群号在手机 QQ 的群资料里能看到。改完记得点下方「保存配置」。"))
        lay.addWidget(groups)

        # ---------------- 网盘目录名
        names = Card()
        names.add(SectionTitle("网盘里的目录名（可选）"))
        self.folder_list = QListWidget()
        self.folder_list.setMinimumHeight(110)
        names.add(self.folder_list)

        nrow = names.row()
        btn_fetch = QPushButton("从 QQ 拉取群名")
        btn_fetch.clicked.connect(self._load_names)
        nrow.addWidget(btn_fetch)
        btn_gen = QPushButton("📋  按群名一键生成")
        btn_gen.setObjectName("Primary")
        btn_gen.clicked.connect(self._gen_folder_names)
        nrow.addWidget(btn_gen)
        nrow.addStretch(1)
        names.add(Hint(
            "群号在网盘里很难辨认 —— 点「按群名一键生成」会把每个群映射成群名"
            "（非法字符自动替换）。生成后如撞名会有提示，手工改几个即可。"
        ))
        lay.addWidget(names)

        # ---------------- 筛选规则
        rules = Card()
        rules.add(SectionTitle("文件名过滤（正则）"))

        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        g.addWidget(QLabel("包含（命中任意一条即通过）"), 0, 0)
        self.include = QPlainTextEdit()
        self.include.setPlaceholderText(r".*\.(pdf|xlsx)$")
        self.include.setFixedHeight(70)
        g.addWidget(self.include, 0, 1)
        g.addWidget(QLabel("排除（命中任意一条即丢弃，优先级更高）"), 1, 0)
        self.exclude = QPlainTextEdit()
        self.exclude.setPlaceholderText(r".*(~\$|\.tmp$).*")
        self.exclude.setFixedHeight(70)
        g.addWidget(self.exclude, 1, 1)
        rules.body.addLayout(g)

        trow = rules.row()
        trow.addWidget(QLabel("常用模板："))
        self.template = QComboBox()
        for name, _pattern in TEMPLATES:
            self.template.addItem(name)
        trow.addWidget(self.template)
        btn_insert = QPushButton("插入到「包含」")
        btn_insert.setObjectName("Ghost")
        btn_insert.clicked.connect(self._insert_template)
        trow.addWidget(btn_insert)
        btn_dry = QPushButton("🔍  规则试跑")
        btn_dry.clicked.connect(self._dry_run)
        trow.addWidget(btn_dry)
        trow.addStretch(1)
        rules.add(Hint("「规则试跑」用一组样例文件名跑规则，看哪些会被搬走、哪些被跳过。"))
        lay.addWidget(rules)

        # ---------------- 类型与体积
        limits = Card()
        limits.add(SectionTitle("类型与体积限制"))
        lg = QGridLayout()
        lg.setHorizontalSpacing(12)
        lg.setVerticalSpacing(8)

        lg.addWidget(QLabel("扩展名白名单（逗号分隔）"), 0, 0)
        self.exts = QLineEdit()
        self.exts.setPlaceholderText("pdf, xlsx（留空表示不限制）")
        lg.addWidget(self.exts, 0, 1)

        lg.addWidget(QLabel("最小体积（MB）"), 1, 0)
        self.min_mb = QLineEdit()
        self.min_mb.setPlaceholderText("0 = 不限")
        lg.addWidget(self.min_mb, 1, 1)

        lg.addWidget(QLabel("最大体积（MB）"), 2, 0)
        self.max_mb = QLineEdit()
        self.max_mb.setPlaceholderText("0 = 不限；百度驱动建议 1900")
        lg.addWidget(self.max_mb, 2, 1)
        limits.body.addLayout(lg)
        lay.addWidget(limits)

        # ---------------- 试跑结果
        self.dry_card = Card()
        self.dry_card.add(SectionTitle("规则试跑结果"))
        self.dry_tree = QTreeWidget()
        self.dry_tree.setColumnCount(4)
        self.dry_tree.setHeaderLabels(["文件名", "体积(MB)", "结果", "原因"])
        self.dry_tree.setMinimumHeight(150)
        self.dry_card.add(self.dry_tree)
        lay.addWidget(self.dry_card)

        # ---------------- 保存
        srow = QHBoxLayout()
        btn_save = QPushButton("💾  保存配置")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save)
        srow.addWidget(btn_save)
        srow.addStretch(1)
        lay.addLayout(srow)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 载入

    def on_start(self) -> None:
        cfg = self.controller.config
        self.group_list.clear()
        for gid in cfg.groups:
            self.group_list.addItem(str(gid))
        self.group_count.setText(f"共 {len(cfg.groups)} 个群")

        flt = cfg.filters
        self.include.setPlainText("\n".join(flt.include or []))
        self.exclude.setPlainText("\n".join(flt.exclude or []))
        self.exts.setText(", ".join(flt.extensions or []))
        self.min_mb.setText(str(flt.min_size_mb or 0))
        self.max_mb.setText(str(flt.max_size_mb or 0))
        self._reload_folders()

    def _reload_folders(self) -> None:
        cfg = self.controller.config
        try:
            names = self.controller.cached_group_names() or {}
        except Exception:  # noqa: BLE001
            names = {}
        self.folder_list.clear()
        for gid in cfg.groups:
            mapped = cfg.group_folder_names.get(str(gid), "")
            self.folder_list.addItem(
                f"{gid}　群名：{names.get(str(gid), '未获取')}　→　目录：{mapped or '（自动）'}"
            )

    # ------------------------------------------------------------ 群号

    def _add_group(self) -> None:
        gid = self.new_group.text().strip()
        if not gid.isdigit() or len(gid) < 5:
            self.toast.show("群号应为 5 位以上数字", "warning")
            return
        if gid in self.controller.config.groups:
            self.toast.show("这个群号已经在列表里了", "info")
            return
        self.controller.config.groups.append(gid)
        self.new_group.clear()
        self.on_start()
        self.toast.show(f"已添加群 {gid}（记得点「保存配置」）", "success")

    def _remove_group(self) -> None:
        item = self.group_list.currentItem()
        if item is None:
            self.toast.show("请先在列表里选中一个群", "warning")
            return
        gid = item.text().strip()
        if gid in self.controller.config.groups:
            self.controller.config.groups.remove(gid)
        self.on_start()
        self.toast.show(f"已移除群 {gid}（记得点「保存配置」）", "info")

    # ------------------------------------------------------------ 目录名

    def _load_names(self) -> None:
        try:
            ok = self.controller.load_group_names()
            self.toast.show("群名已更新" if ok else "拉取群名失败（QQ 未登录？）",
                            "success" if ok else "warning")
            self._reload_folders()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"拉取失败：{type(exc).__name__}", "error")

    def _gen_folder_names(self) -> None:
        """按群名生成目录名映射（旧 Tk 版同款功能）。"""
        cfg = self.controller.config
        try:
            names = self.controller.cached_group_names() or {}
        except Exception:  # noqa: BLE001
            names = {}
        if not names:
            self.toast.show("还没有群名 —— 先点「从 QQ 拉取群名」", "warning")
            return

        mapping: dict[str, str] = {}
        for gid in cfg.groups:
            raw = names.get(str(gid), "")
            if raw:
                mapping[str(gid)] = sanitize_folder(raw)
        cfg.group_folder_names = mapping

        if has_folder_conflicts(parse_folder_map(cfg.group_folder_names)):
            self.toast.show("有群名重复、目录会撞名 —— 请手工改几个", "warning")
        else:
            self.toast.show(
                f"已为 {len(cfg.group_folder_names)} 个群生成目录名（记得保存）", "success")
        self._reload_folders()

    # ------------------------------------------------------------ 规则

    def _apply_filter_widgets(self) -> None:
        flt = self.controller.config.filters
        flt.include = [l.strip() for l in self.include.toPlainText().splitlines() if l.strip()]
        flt.exclude = [l.strip() for l in self.exclude.toPlainText().splitlines() if l.strip()]
        flt.extensions = [
            e.strip().lstrip(".").lower()
            for e in self.exts.text().replace("，", ",").split(",")
            if e.strip()
        ]
        try:
            flt.min_size_mb = float(self.min_mb.text() or 0)
            flt.max_size_mb = float(self.max_mb.text() or 0)
        except ValueError:
            flt.min_size_mb = flt.max_size_mb = 0.0

    def _insert_template(self) -> None:
        name = self.template.currentText()
        pattern = next((p for n, p in TEMPLATES if n == name), "")
        if not pattern:
            return
        lines = [l for l in self.include.toPlainText().splitlines() if l.strip()]
        if pattern not in lines:
            lines.append(pattern)
        self.include.setPlainText("\n".join(lines))
        self.toast.show("模板已插入「包含」（记得保存）", "info")

    def _dry_run(self) -> None:
        self._apply_filter_widgets()
        try:
            compiled = CompiledFilters(self.controller.config.filters)
            results = compiled.selftest()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"规则无效：{type(exc).__name__}: {exc}", "error")
            return

        self.dry_tree.clear()
        for row in results:
            self.dry_tree.addTopLevelItem(QTreeWidgetItem([
                str(row.get("name", "")),
                str(row.get("size_mb", "")),
                "通过" if row.get("accepted") else "跳过",
                str(row.get("reason", "")),
            ]))
        for i in range(4):
            self.dry_tree.resizeColumnToContents(i)
        ok = sum(1 for r in results if r.get("accepted"))
        self.toast.show(f"试跑完成：{ok}/{len(results)} 个样例会通过", "info")

    # ------------------------------------------------------------ 保存

    def _save(self) -> None:
        self._apply_filter_widgets()
        try:
            problems = self.controller.save_settings()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"保存失败：{type(exc).__name__}", "error")
            return
        if problems:
            self.toast.show("已保存，但有问题：" + "；".join(problems[:2]), "warning")
        else:
            self.toast.show("配置已保存", "success")
