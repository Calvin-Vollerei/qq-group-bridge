"""QQ 登录页：组件托管、二维码登录、状态与令牌。"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
from .widgets import Card, Hint, SectionTitle, StatusBadge

log = logging.getLogger(__name__)


class QQPage(Page):
    """QQ 组件与扫码登录。

    二维码来自控制器的 ``qrcode`` 属性（PNG 字节）；控制器已经处理了
    "新请求取代旧请求"的语义（见 controller.fetch_qrcode 的说明）。
    """

    title = "QQ 登录"

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

        middle = QHBoxLayout()
        middle.setSpacing(12)

        # ---------------- 左：组件状态与控制
        comp = Card()
        head = comp.row()
        head.addWidget(SectionTitle("QQ 组件（NapCat）"))
        head.addStretch(1)
        self.comp_badge = StatusBadge("未知")
        head.addWidget(self.comp_badge)

        self.comp_info = Hint("")
        comp.add(self.comp_info)

        row = comp.row()
        self.btn_start = QPushButton("启动组件")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self._start)
        row.addWidget(self.btn_start)

        btn_stop = QPushButton("停止")
        btn_stop.clicked.connect(self._stop)
        row.addWidget(btn_stop)

        btn_refresh = QPushButton("刷新状态")
        btn_refresh.setObjectName("Ghost")
        btn_refresh.clicked.connect(self.refresh)
        row.addWidget(btn_refresh)

        btn_dir = QPushButton("打开安装目录")
        btn_dir.setObjectName("Ghost")
        btn_dir.clicked.connect(self._open_dir)
        row.addWidget(btn_dir)

        comp.add(Hint(
            "第一次使用需要先「启动组件」，它会联网准备 QQ 运行环境。\n"
            "状态一直是「未安装」时，请用旧界面（--ui tk）里的安装向导，"
            "或手动解压自带的组件包。"
        ))
        middle.addWidget(comp, 1)

        # ---------------- 右：二维码
        qr = Card()
        qr.add(SectionTitle("扫码登录"))
        self.qr_label = QLabel("尚未获取二维码")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(220, 220)
        self.qr_label.setStyleSheet(
            "QLabel { background: rgba(255,255,255,0.92); color: #333;"
            " border-radius: 12px; }"
        )
        qr.add(self.qr_label, 1)

        self.qr_hint = Hint("点「获取/刷新二维码」，然后用手机 QQ（建议小号）扫码。")
        qr.add(self.qr_hint)

        qrow = qr.row()
        self.btn_qr = QPushButton("① 获取/刷新二维码")
        self.btn_qr.setObjectName("Primary")
        self.btn_qr.clicked.connect(self._fetch_qr)
        qrow.addWidget(self.btn_qr)

        btn_probe = QPushButton("检查登录状态")
        btn_probe.clicked.connect(self._probe)
        qrow.addWidget(btn_probe)

        btn_save_qr = QPushButton("保存二维码")
        btn_save_qr.setObjectName("Ghost")
        btn_save_qr.clicked.connect(self._save_qr)
        qrow.addWidget(btn_save_qr)
        middle.addWidget(qr, 1)
        lay.addLayout(middle)

        # ---------------- WebUI 令牌
        token = Card()
        token.add(SectionTitle("组件 WebUI 令牌（排障用）"))
        trow = token.row()
        self.webui_base = QLineEdit()
        self.webui_base.setPlaceholderText("http://127.0.0.1:6099")
        trow.addWidget(self.webui_base, 1)
        btn_find = QPushButton("从组件配置自动读取")
        btn_find.clicked.connect(self._discover_token)
        trow.addWidget(btn_find)
        token.add(Hint(
            "程序会**优先以组件自己的配置为准**读取令牌 —— 切换运行方式后旧令牌会失效，"
            "用旧值只会得到「令牌无效」并触发登录限流。"
        ))
        lay.addWidget(token)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 生命周期

    def on_start(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            status = self.controller.napcat_status()
        except Exception as exc:  # noqa: BLE001
            self.comp_info.setText(f"读取状态失败：{type(exc).__name__}")
            return
        installed = bool(status.get("installed"))
        running = bool(status.get("running"))
        if running:
            self.comp_badge.set_state("ok", "运行中")
        elif installed:
            self.comp_badge.set_state("warn", "已安装")
        else:
            self.comp_badge.set_state("idle", "未安装")
        self.comp_info.setText(
            f"安装目录：{status.get('install_dir') or '—'}\n"
            f"启动器：{status.get('launcher') or '—'}"
            + (f"\nPID：{status.get('pid')}" if status.get("pid") else "")
        )
        try:
            self.webui_base.setText(self.controller.napcat_webui_url())
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 动作

    def _start(self) -> None:
        try:
            ok = self.controller.start_napcat()
            self.toast.show("组件启动中…" if ok else "启动未生效，请看下方状态", 
                            "success" if ok else "warning")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"启动失败：{type(exc).__name__}: {exc}", "error")

    def _stop(self) -> None:
        try:
            self.controller.stop_napcat()
            self.toast.show("已停止组件", "info")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"停止失败：{type(exc).__name__}", "error")

    def _open_dir(self) -> None:
        import os
        import subprocess
        import sys

        try:
            path = self.controller.napcat_status().get("install_dir") or ""
            if not path:
                self.toast.show("还不知道组件安装目录", "warning")
                return
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                subprocess.Popen(["xdg-open", path])  # noqa: S603,S607
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"打开失败：{type(exc).__name__}", "error")

    def _fetch_qr(self) -> None:
        self.btn_qr.setEnabled(False)
        self.qr_hint.setText("正在获取二维码…（最长等 25 秒，可重复点击刷新）")
        try:
            started = self.controller.fetch_qrcode()
        except Exception as exc:  # noqa: BLE001
            self.btn_qr.setEnabled(True)
            self.toast.show(f"获取失败：{type(exc).__name__}", "error")
            return
        if not started:
            self.btn_qr.setEnabled(True)
            self.toast.show("请求未能启动，请稍后重试", "warning")

    def _probe(self) -> None:
        try:
            self.controller.probe_qq()
            state = self.controller.qq_login_state() or {}
            self.toast.show(str(state.get("message") or "已查询登录状态"), "info")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"查询失败：{type(exc).__name__}", "error")

    def _save_qr(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        qr = getattr(self.controller, "qrcode", None)
        data = getattr(qr, "png_bytes", None) if qr is not None else None
        if not data:
            self.toast.show("请先获取二维码", "warning")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存二维码", "qq-qrcode.png", "PNG 图片 (*.png)")
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(data)
            self.toast.show("二维码已保存", "success")
        except OSError as exc:
            self.toast.show(f"保存失败：{exc}", "error")

    def _discover_token(self) -> None:
        try:
            ok = self.controller.discover_webui_token()
            self.toast.show("已从组件配置读取令牌" if ok else "没找到令牌，请手动填写",
                            "success" if ok else "warning")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"读取失败：{type(exc).__name__}", "error")

    # ------------------------------------------------------------ 事件

    def on_event(self, event) -> None:
        kind = getattr(event, "kind", "")
        if kind == "qr":
            if event.data.get("pending"):
                self.qr_hint.setText("正在获取二维码…（最长等 25 秒，可重复点击刷新）")
                return
            self.btn_qr.setEnabled(True)
            qr = getattr(self.controller, "qrcode", None)
            if qr is not None and getattr(qr, "png_bytes", None):
                self._render(qr.png_bytes)
                self.qr_hint.setText(
                    "请用手机 QQ 扫码。二维码约 30 秒失效 —— "
                    "过期就点「① 获取/刷新二维码」重取（可连点）。"
                )
            else:
                self.qr_label.setText("二维码不可用")
                hint = str(event.data.get("hint") or "")
                self.qr_hint.setText(
                    f"{getattr(event, 'message', '')}\n{hint}\n"
                    "可以再点一次重试。".strip()
                )
        elif kind == "napcat":
            self.refresh()
        elif kind == "login":
            if event.data.get("category") == "online":
                self.qr_hint.setText("✅ 已登录成功。回「监控」页点「开始监控」即可。")
                self.toast.show("QQ 登录成功", "success")
            self.refresh()

    def _render(self, png_bytes: bytes) -> None:
        from PySide6.QtGui import QPixmap

        pix = QPixmap()
        if not pix.loadFromData(png_bytes, "PNG"):
            self.qr_label.setText("二维码解码失败")
            return
        side = max(160, min(self.qr_label.width(), self.qr_label.height()) - 12)
        self.qr_label.setPixmap(
            pix.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        )
