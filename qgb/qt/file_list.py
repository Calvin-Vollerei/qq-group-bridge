"""文件列表与下载顺序对话框（监控页的「📋 文件列表 / 下载顺序」）。

补齐旧 Tk 版功能（对照 qgb/gui/tab_monitor.py 的 _show_records）：
  排序（下载顺序/文件名/群号/大小/上传时间/处理时间）+ 倒序
  搜索（文件名/群号）+ 筛选（状态/群聊/大小）
  ★置顶 / 取消置顶 / ↑上移 / ↓下移 / ⤒优先执行筛选结果 / 刷新
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..models import TransferState
from ..utils import human_size

log = logging.getLogger(__name__)

STATE_LABEL = {
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

SIZE_BUCKETS = ("全部", "< 1 MB", "1–10 MB", "10–100 MB", "≥ 100 MB")


class FileListDialog(QDialog):
    """文件列表：排序 / 搜索 / 筛选 / 置顶 / 移动 / 优先执行。"""

    def __init__(self, page) -> None:
        super().__init__(page)
        self.page = page
        self.controller = page.controller
        self.setWindowTitle("文件列表与下载顺序")
        self.resize(1180, 720)
        # 非模态 + 独立窗口：用户要求"打开列表后仍能操作主页面"
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Window, True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        # ---------------- 排序 / 搜索
        top = QHBoxLayout()
        top.addWidget(QLabel("排序："))
        self.sort_box = QComboBox()
        for key, label in (
            ("queue", "下载顺序"), ("name", "文件名"), ("group", "群号"),
            ("size", "大小"), ("uptime", "上传时间"), ("updated", "处理时间"),
        ):
            self.sort_box.addItem(label, key)
        self.sort_box.currentIndexChanged.connect(self.refresh)
        top.addWidget(self.sort_box)
        self.desc = QCheckBox("倒序")
        self.desc.stateChanged.connect(self.refresh)
        top.addWidget(self.desc)

        top.addWidget(QLabel("　搜索："))
        self.search = QLineEdit()
        self.search.setPlaceholderText("文件名或群号")
        self.search.textChanged.connect(self.refresh)
        top.addWidget(self.search, 1)
        lay.addLayout(top)

        # ---------------- 筛选
        filt = QHBoxLayout()
        filt.addWidget(QLabel("筛选：状态"))
        self.status_box = QComboBox()
        self.status_box.addItem("全部")
        for label in dict.fromkeys(STATE_LABEL.values()):
            self.status_box.addItem(label)
        self.status_box.currentIndexChanged.connect(self.refresh)
        filt.addWidget(self.status_box)

        filt.addWidget(QLabel("群聊"))
        self.group_box = QComboBox()
        self.group_box.addItem("全部")
        self.group_box.currentIndexChanged.connect(self.refresh)
        filt.addWidget(self.group_box)

        filt.addWidget(QLabel("大小"))
        self.size_box = QComboBox()
        for label in SIZE_BUCKETS:
            self.size_box.addItem(label)
        self.size_box.currentIndexChanged.connect(self.refresh)
        filt.addWidget(self.size_box)

        btn_prio = QPushButton("⤒ 优先执行筛选结果")
        btn_prio.setObjectName("Primary")
        btn_prio.clicked.connect(self._prioritize)
        filt.addWidget(btn_prio)
        btn_clear = QPushButton("清除筛选")
        btn_clear.setObjectName("Ghost")
        btn_clear.clicked.connect(self._clear_filters)
        filt.addWidget(btn_clear)
        filt.addStretch(1)
        lay.addLayout(filt)

        # ---------------- 表格
        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            ["", "文件名", "群号", "大小", "上传时间", "状态", "处理时间"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        lay.addWidget(self.tree, 1)

        # ---------------- 动作
        bottom = QHBoxLayout()
        self.info = QLabel("")
        self.info.setObjectName("Hint")
        bottom.addWidget(self.info)
        bottom.addStretch(1)
        for text, slot, style in (
            ("🔄 刷新列表", self.refresh, "ghost"),
            ("★ 置顶", self._pin, "primary"),
            ("取消置顶", self._unpin, "ghost"),
            ("↑ 上移", lambda: self._move(-1), "ghost"),
            ("↓ 下移", lambda: self._move(+1), "ghost"),
        ):
            btn = QPushButton(text)
            if style == "primary":
                btn.setObjectName("Primary")
            btn.clicked.connect(slot)
            bottom.addWidget(btn)
        lay.addLayout(bottom)

        self.filters = page._flt  # 复用页面上的筛选状态（关窗不丢）
        self._restore_filters()
        self.refresh()

    # ------------------------------------------------------------ 筛选状态

    def _restore_filters(self) -> None:
        f = self.filters
        for box, key in ((self.sort_box, "sort"), (self.status_box, "status"),
                         (self.group_box, "group"), (self.size_box, "size")):
            idx = box.findText(f.get(key, ""))
            if idx >= 0:
                box.blockSignals(True)
                box.setCurrentIndex(idx)
                box.blockSignals(False)
        self.search.setText(f.get("search", ""))
        self.desc.setChecked(bool(f.get("desc", False)))

    def _save_filters(self) -> None:
        self.filters.update({
            "sort": self.sort_box.currentText(),
            "status": self.status_box.currentText(),
            "group": self.group_box.currentText(),
            "size": self.size_box.currentText(),
            "search": self.search.text(),
            "desc": self.desc.isChecked(),
        })

    def _clear_filters(self) -> None:
        self.status_box.setCurrentIndex(0)
        self.group_box.setCurrentIndex(0)
        self.size_box.setCurrentIndex(0)
        self.search.clear()
        self.desc.setChecked(False)

    # ------------------------------------------------------------ 数据

    def _selected_keys(self) -> list[tuple[str, int, str]]:
        keys = []
        for item in self.tree.selectedItems():
            raw = item.data(0, Qt.ItemDataRole.UserRole)
            if raw:
                g, b, f = str(raw).split("\x1f")
                keys.append((g, int(b), f))
        return keys

    def refresh(self) -> None:
        self._save_filters()
        try:
            rows = self.controller.list_files(search=self.search.text().strip())
        except Exception as exc:  # noqa: BLE001
            log.debug("取文件列表失败：%s", exc)
            rows = []

        # 群聊下拉按当前数据自动填充
        groups = sorted({str(r.get("group_id", "")) for r in rows if r.get("group_id")})
        want = ["全部"] + groups
        if [self.group_box.itemText(i) for i in range(self.group_box.count())] != want:
            keep = self.group_box.currentText()
            self.group_box.blockSignals(True)
            self.group_box.clear()
            self.group_box.addItems(want)
            idx = self.group_box.findText(keep)
            self.group_box.setCurrentIndex(max(0, idx))
            self.group_box.blockSignals(False)

        want_status = self.status_box.currentText()
        want_group = self.group_box.currentText()
        bucket = self.size_box.currentText()

        def size_ok(r) -> bool:
            if bucket == "全部":
                return True
            mb = int(r.get("size") or 0) / 1048576
            if bucket == "< 1 MB":
                return mb < 1
            if bucket == "1–10 MB":
                return 1 <= mb < 10
            if bucket == "10–100 MB":
                return 10 <= mb < 100
            return mb >= 100

        if want_status != "全部":
            rows = [r for r in rows
                    if STATE_LABEL.get(str(r.get("state", "")), "") == want_status]
        if want_group != "全部":
            rows = [r for r in rows if str(r.get("group_id", "")) == want_group]
        rows = [r for r in rows if size_ok(r)]

        key = self.sort_box.currentData()
        desc = self.desc.isChecked()
        if key == "name":
            rows.sort(key=lambda r: str(r.get("name", "")).lower(), reverse=desc)
        elif key == "size":
            rows.sort(key=lambda r: int(r.get("size") or 0), reverse=desc)
        elif key == "group":
            rows.sort(key=lambda r: str(r.get("group_id", "")), reverse=desc)
        elif key == "uptime":
            rows.sort(key=lambda r: float(r.get("upload_time") or 0), reverse=desc)
        elif key == "updated":
            rows.sort(key=lambda r: float(r.get("updated_at") or 0), reverse=desc)

        self.tree.clear()
        pending = 0
        for r in rows:
            state = str(r.get("state", ""))
            if state == TransferState.DISCOVERED.value:
                pending += 1
            item = QTreeWidgetItem([
                "★" if r.get("pinned") else "",
                str(r.get("name", "")),
                str(r.get("group_id", "")),
                human_size(r.get("size", 0)),
                str(r.get("uploader_name") or ""),
                STATE_LABEL.get(state, state),
                str(r.get("error") or "")[:60],
            ])
            item.setData(0, Qt.ItemDataRole.UserRole,
                         f"{r['group_id']}\x1f{r['busid']}\x1f{r['file_id']}")
            self.tree.addTopLevelItem(item)
        for i in range(7):
            self.tree.resizeColumnToContents(i)
        self.tree.setColumnWidth(1, max(260, self.tree.columnWidth(1)))
        self.info.setText(f"筛选后 {len(rows)} 项，其中待处理 {pending} 项")

    # ------------------------------------------------------------ 动作

    def _act(self, fn, label: str) -> None:
        keys = self._selected_keys()
        if not keys:
            self.page.toast.show("请先选中一行", "warning")
            return
        changed = sum(1 for k in keys if fn(k))
        if changed == 0:
            self.page.toast.show(
                f"{label}未生效：可能已到边界，或该项不是「待处理」状态", "info")
        else:
            self.page.toast.show(f"{label}：{changed} 项", "success")
        self.refresh()

    def _pin(self) -> None:
        self._act(lambda k: self.controller.queue_pin(k, True), "已置顶")

    def _unpin(self) -> None:
        self._act(lambda k: self.controller.queue_pin(k, False), "已取消置顶")

    def _move(self, delta: int) -> None:
        self._act(lambda k: self.controller.queue_move(k, delta),
                  "已上移" if delta < 0 else "已下移")

    def _prioritize(self) -> None:
        keys = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.text(5) != "待处理":
                continue
            raw = item.data(0, Qt.ItemDataRole.UserRole)
            if raw:
                g, b, f = str(raw).split("\x1f")
                keys.append((g, int(b), f))
        if not keys:
            self.page.toast.show("当前筛选结果里没有「待处理」的文件", "info")
            return
        moved = self.controller.prioritize_filtered(keys)
        self.page.toast.show(f"已把 {moved} 个文件排到队列前面，会优先搬运", "success")
        self.refresh()
