"""群与规则页：群号增删、群名、过滤规则。"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .shell import Page
from .widgets import Card, Hint, SectionTitle

log = logging.getLogger(__name__)


class GroupsPage(Page):
    """群号与过滤规则。

    读写都走 ``controller.config``（AppConfig），保存时调用控制器的配置保存；
    这样与旧 Tk 界面共用同一份配置，互不干扰。
    """

    title = "群与规则"

    def build(self) -> QWidget:
        area = self._scroll_area()
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
        self.group_list.setMinimumHeight(140)
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

        groups.add(Hint(
            "群号在手机 QQ 的群资料里能看到。多个群就用「添加」逐个加入。"
            "右键或选中后点「删除选中」移除。"
        ))
        lay.addWidget(groups)

        # ---------------- 群名（用于网盘子目录命名）
        names = Card()
        names.add(SectionTitle("群名（可选，便于在网盘里辨认）"))
        self.name_list = QListWidget()
        self.name_list.setMinimumHeight(110)
        names.add(self.name_list)
        row = names.row()
        btn_load = QPushButton("从 QQ 拉取群名")
        btn_load.clicked.connect(self._load_names)
        row.addWidget(btn_load)
        row.addStretch(1)
        names.add(Hint("拉取后会缓存下来；到「网盘与凭据」页可选用「群名」作为子目录。"))
        lay.addWidget(names)

        # ---------------- 过滤规则
        flt = Card()
        flt.add(SectionTitle("过滤规则"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("包含（一行一条正则）"), 0, 0)
        self.include = QPlainTextEdit()
        self.include.setPlaceholderText(r".*\.(pdf|xlsx)$")
        self.include.setFixedHeight(74)
        grid.addWidget(self.include, 0, 1)

        grid.addWidget(QLabel("排除（一行一条正则）"), 1, 0)
        self.exclude = QPlainTextEdit()
        self.exclude.setPlaceholderText(r".*\.(tmp|log)$")
        self.exclude.setFixedHeight(74)
        grid.addWidget(self.exclude, 1, 1)

        grid.addWidget(QLabel("体积上限（MB）"), 2, 0)
        self.max_mb = QLineEdit()
        self.max_mb.setPlaceholderText("0 = 不限")
        grid.addWidget(self.max_mb, 2, 1)

        grid.addWidget(QLabel("体积下限（MB）"), 3, 0)
        self.min_mb = QLineEdit()
        self.min_mb.setPlaceholderText("0 = 不限")
        grid.addWidget(self.min_mb, 3, 1)
        flt.body.addLayout(grid)

        self.recursive = QCheckBox("同时监控群文件夹（子目录）里的文件")
        flt.add(self.recursive)
        flt.add(Hint(
            "包含/排除填的是**正则表达式**；留空表示不限制。"
            "不确定就先只填包含，例如 .*\\.(pdf|xlsx)$ 只搬 PDF 和 Excel。"
        ))
        lay.addWidget(flt)

        # ---------------- 保存
        save_row = QHBoxLayout()
        btn_save = QPushButton("保存配置")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save)
        save_row.addWidget(btn_save)
        save_row.addStretch(1)
        lay.addLayout(save_row)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 工具

    def _scroll_area(self) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setStyleSheet(
            "QScrollArea, QScrollArea > QWidget > QWidget, QScrollArea > QWidget "
            "{ background: transparent; border: none; }"
        )
        area.viewport().setAutoFillBackground(False)
        return area

    # ------------------------------------------------------------ 生命周期

    def on_start(self) -> None:
        self._reload()

    def _reload(self) -> None:
        cfg = self.controller.config
        self.group_list.clear()
        for gid in cfg.groups:
            self.group_list.addItem(str(gid))
        self.group_count.setText(f"共 {len(cfg.groups)} 个群")

        flt = cfg.filters
        self.include.setPlainText("\n".join(flt.include or []))
        self.exclude.setPlainText("\n".join(flt.exclude or []))
        self.max_mb.setText(str(flt.max_size_mb or 0))
        self.min_mb.setText(str(flt.min_size_mb or 0))
        self.recursive.setChecked(bool(cfg.monitor.recursive_folders))

        self._reload_names()

    def _reload_names(self) -> None:
        self.name_list.clear()
        try:
            names = self.controller.cached_group_names() or {}
        except Exception:  # noqa: BLE001
            names = {}
        for gid in self.controller.config.groups:
            self.name_list.addItem(f"{gid}　→　{names.get(str(gid), '（未获取）')}")

    # ------------------------------------------------------------ 动作

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
        self._reload()
        self.toast.show(f"已添加群 {gid}（记得点「保存配置」）", "success")

    def _remove_group(self) -> None:
        item = self.group_list.currentItem()
        if item is None:
            self.toast.show("请先在列表里选中一个群", "warning")
            return
        gid = item.text().strip()
        if gid in self.controller.config.groups:
            self.controller.config.groups.remove(gid)
        self._reload()
        self.toast.show(f"已移除群 {gid}（记得点「保存配置」）", "info")

    def _load_names(self) -> None:
        try:
            ok = self.controller.load_group_names()
            self.toast.show("群名已更新" if ok else "拉取群名失败（QQ 未登录？）",
                            "success" if ok else "warning")
            self._reload_names()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"拉取失败：{type(exc).__name__}", "error")

    def _save(self) -> None:
        cfg = self.controller.config
        cfg.filters.include = [l.strip() for l in self.include.toPlainText().splitlines()
                               if l.strip()]
        cfg.filters.exclude = [l.strip() for l in self.exclude.toPlainText().splitlines()
                               if l.strip()]
        try:
            cfg.filters.max_size_mb = float(self.max_mb.text() or 0)
            cfg.filters.min_size_mb = float(self.min_mb.text() or 0)
        except ValueError:
            self.toast.show("体积要填数字（0 表示不限）", "warning")
            return
        cfg.monitor.recursive_folders = self.recursive.isChecked()

        problems = []
        try:
            problems = cfg.validate()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.controller.save_settings()
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", f"{type(exc).__name__}: {exc}")
            return
        if problems:
            self.toast.show("已保存，但有问题：" + "；".join(problems[:2]), "warning")
        else:
            self.toast.show("配置已保存", "success")
