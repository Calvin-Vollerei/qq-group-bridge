"""高级页：完整设置项 + 环境自检 + 目录直达 + 维护操作。

补齐旧 Tk 版的功能（对照 qgb/gui/tab_advanced.py）：
  监控节奏：轮询间隔 / 抖动 / 并发下载数 / 每页拉取条数 / 抓取子目录
  失败处理：最大重试次数 / 重试退避基数 / 本地副本保留天数 / 下载卡死阈值
  资源保护：单文件体积上限 / 最低剩余磁盘 / 日志保留天数
  运行环境：自检报告 / 打开数据目录 / 打开日志目录 / 打开临时目录
  维护    ：重试失败项 / 清空记录并重新发现 / 立即刷新

外观相关的说明与切换器已按要求去掉（主题统一、模糊固定 aero）。
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .shell import Page
from .widgets import Card, Hint, SectionTitle

log = logging.getLogger(__name__)


class NumField(QLineEdit):
    """数字输入（数值非法时回落到默认值，不抛异常）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(110)

    def value_int(self, default: int, minimum: int = 0) -> int:
        try:
            return max(minimum, int(float(self.text() or default)))
        except ValueError:
            return default

    def value_float(self, default: float) -> float:
        try:
            return max(0.0, float(self.text() or default))
        except ValueError:
            return default


class AdvancedPage(Page):
    title = "高级"

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

        self.f: dict[str, NumField] = {}

        def grid_card(title: str, rows: list[tuple[str, str, str]]) -> Card:
            """rows = [(字段键, 占位提示, 中文标签)]"""
            card = Card()
            card.add(SectionTitle(title))
            g = QGridLayout()
            g.setHorizontalSpacing(12)
            g.setVerticalSpacing(8)
            for i, (key, hint, note) in enumerate(rows):
                g.addWidget(QLabel(note), i, 0)
                field = NumField()
                field.setPlaceholderText(hint)
                self.f[key] = field
                g.addWidget(field, i, 1)
            card.body.addLayout(g)
            return card

        # ---------------- 监控节奏
        pace = grid_card("监控节奏", [
            ("poll_interval_sec", "300", "轮询间隔（秒）"),
            ("jitter_sec", "30", "轮询抖动（秒）"),
            ("download_concurrency", "1", "并发下载数"),
            ("page_size", "50", "每页拉取条数"),
        ])
        self.recursive = QCheckBox("抓取群文件夹（子目录）内的文件")
        pace.add(self.recursive)
        pace.add(Hint("间隔越短越容易被风控；抖动用于打散访问节律。并发数建议保持 1。"))
        lay.addWidget(pace)

        # ---------------- 失败处理
        rel = grid_card("失败处理", [
            ("max_retries", "3", "单文件最大重试次数"),
            ("retry_backoff_sec", "30", "重试退避基数（秒）"),
            ("keep_local_days", "0", "本地副本保留天数"),
            ("stall_timeout_sec", "20.0", "下载卡死阈值（秒）"),
        ])
        rel.add(Hint(
            "退避按「第 N 次重试等待 N × 基数」计算。\n"
            "本地副本保留 0 = 上传成功后立即删除（上传失败的副本会保留复用，不会重下）。\n"
            "卡死阈值：连续这么久没收到数据就放弃该文件、继续下一个（最低 5 秒）。"
        ))
        lay.addWidget(rel)

        # ---------------- 资源保护
        lay.addWidget(grid_card("资源保护", [
            ("max_file_mb", "0", "单文件体积上限（MB，0=不限）"),
            ("min_free_disk_gb", "2.0", "剩余磁盘低于该值则暂停（GB）"),
            ("log_keep_days", "14", "日志保留天数"),
        ]))

        # ---------------- 保存
        save_row = QHBoxLayout()
        btn_save = QPushButton("💾  保存设置")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save)
        save_row.addWidget(btn_save)
        btn_reload = QPushButton("↻  重新载入")
        btn_reload.setObjectName("Ghost")
        btn_reload.clicked.connect(self.on_start)
        save_row.addWidget(btn_reload)
        save_row.addStretch(1)
        lay.addLayout(save_row)

        # ---------------- 运行环境与自检
        env = Card()
        env.add(SectionTitle("运行环境与自检"))
        self.self_check = Hint("（点「重新自检」生成报告）")
        self.self_check.setWordWrap(True)
        env.add(self.self_check)

        env_row = env.row()
        for text, slot in (
            ("↻  重新自检", self._self_check),
            ("打开数据目录", lambda: self._open(self._data_dir())),
            ("打开日志目录", lambda: self._open(self._data_dir() / "logs")),
            ("打开临时目录", lambda: self._open(self._temp_dir())),
        ):
            btn = QPushButton(text)
            btn.setObjectName("Ghost")
            btn.clicked.connect(slot)
            env_row.addWidget(btn)
        env_row.addStretch(1)
        lay.addWidget(env)

        # ---------------- 维护
        maint = Card()
        maint.add(SectionTitle("维护"))
        row = maint.row()
        for text, slot, style in (
            ("重试失败项", self._requeue, "primary"),
            ("清空记录并重新发现", self._reset, "ghost"),
            ("立即刷新（拉取新文件）", self._run_once, "ghost"),
        ):
            btn = QPushButton(text)
            if style == "primary":
                btn.setObjectName("Primary")
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        maint.add(Hint(
            "「清空记录并重新发现」会清掉累计的待处理/已过滤/失败记录"
            "（保留已搬完的去重记录），用于队列越积越多的情况。"
        ))
        lay.addWidget(maint)

        # ---------------- 关于
        about = Card()
        about.add(SectionTitle("关于"))
        try:
            from ..version import APP_NAME, __version__

            about.add(Hint(f"{APP_NAME}　v{__version__}"))
        except Exception:  # noqa: BLE001
            about.add(Hint("QQ群文件搬运工"))
        about.add(Hint(
            "界面：PySide6（Qt 6）+ 原生窗口模糊（aero，固定）\n"
            "旧版 Tk 界面仍可用：run_bridge.py --ui tk"
        ))
        lay.addWidget(about)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 载入 / 保存

    def on_start(self) -> None:
        cfg = self.controller.config
        m = cfg.monitor
        self.f["poll_interval_sec"].setText(str(m.poll_interval_sec))
        self.f["jitter_sec"].setText(str(m.jitter_sec))
        self.f["download_concurrency"].setText(str(m.download_concurrency))
        self.f["page_size"].setText(str(m.page_size))
        self.recursive.setChecked(bool(m.recursive_folders))
        self.f["max_retries"].setText(str(m.max_retries))
        self.f["retry_backoff_sec"].setText(str(m.retry_backoff_sec))
        self.f["keep_local_days"].setText(str(m.keep_local_days))
        self.f["stall_timeout_sec"].setText(str(getattr(m, "stall_timeout_sec", 20.0)))
        self.f["max_file_mb"].setText(str(m.max_file_mb))
        self.f["min_free_disk_gb"].setText(str(m.min_free_disk_gb))
        self.f["log_keep_days"].setText(str(cfg.log_keep_days))

    def _save(self) -> None:
        cfg = self.controller.config
        m = cfg.monitor
        m.poll_interval_sec = max(30, self.f["poll_interval_sec"].value_int(300, 30))
        m.jitter_sec = self.f["jitter_sec"].value_int(30)
        m.download_concurrency = max(1, self.f["download_concurrency"].value_int(1, 1))
        m.page_size = max(1, self.f["page_size"].value_int(50, 1))
        m.recursive_folders = self.recursive.isChecked()
        m.max_retries = max(1, self.f["max_retries"].value_int(3, 1))
        m.retry_backoff_sec = self.f["retry_backoff_sec"].value_int(30)
        m.keep_local_days = self.f["keep_local_days"].value_int(0)
        m.stall_timeout_sec = max(5.0, self.f["stall_timeout_sec"].value_float(20.0))
        m.max_file_mb = self.f["max_file_mb"].value_float(0.0)
        m.min_free_disk_gb = self.f["min_free_disk_gb"].value_float(2.0)
        cfg.log_keep_days = max(1, self.f["log_keep_days"].value_int(14, 1))

        try:
            problems = self.controller.save_settings()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"保存失败：{type(exc).__name__}: {exc}", "error")
            return
        if problems:
            self.toast.show("已保存，但有问题：" + "；".join(problems[:2]), "warning")
        else:
            self.toast.show("设置已保存", "success")

    # ------------------------------------------------------------ 环境

    def _data_dir(self) -> Path:
        from ..paths import default_data_dir

        return default_data_dir()

    def _temp_dir(self) -> Path:
        try:
            return self.controller.config.resolved_temp_dir()
        except Exception:  # noqa: BLE001
            return self._data_dir() / "tmp"

    def _open(self, path) -> None:
        try:
            target = str(path)
            if sys.platform == "win32":
                os.startfile(target)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", target])  # noqa: S603,S607
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"打开失败：{type(exc).__name__}", "error")

    def _self_check(self) -> None:
        """跑一次无界面自检并把报告贴到界面上（旧 Tk 版的「重新自检」）。"""
        self.self_check.setText("正在自检…")
        buf = io.StringIO()
        try:
            from run_bridge import _run_selftest

            with redirect_stdout(buf):
                _run_selftest(None)
            text = buf.getvalue().strip() or "自检完成（无输出）"
        except Exception as exc:  # noqa: BLE001
            text = f"自检失败：{type(exc).__name__}: {exc}"
        self.self_check.setText(text)

    # ------------------------------------------------------------ 维护

    def _requeue(self) -> None:
        try:
            n = self.controller.requeue_failed()
            self.toast.show(f"已把 {n} 个失败项重新排队", "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"操作失败：{type(exc).__name__}", "error")

    def _reset(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(
            self, "清空记录并重新发现",
            "将清空「待处理 / 已过滤 / 失败」的累计记录，然后重新扫描群文件。\n\n"
            "· 已搬完的记录会保留（用于去重，不会重复上传）\n"
            "· 下次扫描会按当前群文件重新建立待办列表\n\n确定继续吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.controller.reset_discovery()
            self.toast.show(
                f"已清空 {result.get('removed', 0)} 条累计记录，点「立即刷新」重新发现",
                "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"清空失败：{type(exc).__name__}", "error")

    def _run_once(self) -> None:
        try:
            started = self.controller.run_once_now()
            self.toast.show("已开始一轮扫描" if started else "当前一轮还没结束，已跳过",
                            "success" if started else "warning")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"刷新失败：{type(exc).__name__}", "error")
