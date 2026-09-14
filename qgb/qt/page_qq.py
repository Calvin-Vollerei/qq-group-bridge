"""QQ 登录页：组件托管、扫码登录、连接参数。

补齐旧 Tk 版功能（对照 qgb/gui/tab_qq.py）：
  启动/停止组件、从压缩包安装、打开安装目录、获取/保存二维码、
  检查登录状态、打开 NapCat 网页版（备用扫码入口）、
  连接参数（OneBot API / WebUI 地址 / OneBot 令牌 / WebUI 令牌）、
  保存令牌（加密）、从 NapCat 配置自动读取令牌。
"""

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

        # ---------------- 左：组件
        comp = Card()
        head = comp.row()
        head.addWidget(SectionTitle("QQ 组件（NapCat）"))
        head.addStretch(1)
        self.comp_badge = StatusBadge("未知")
        head.addWidget(self.comp_badge)

        self.comp_info = Hint("")
        comp.add(self.comp_info)

        r1 = comp.row()
        self.btn_start = QPushButton("启动组件")
        self.btn_start.setObjectName("Primary")
        self.btn_start.clicked.connect(self._start)
        r1.addWidget(self.btn_start)
        btn_stop = QPushButton("停止")
        btn_stop.clicked.connect(self._stop)
        r1.addWidget(btn_stop)
        btn_refresh = QPushButton("刷新状态")
        btn_refresh.setObjectName("Ghost")
        btn_refresh.clicked.connect(self.refresh)
        r1.addWidget(btn_refresh)

        r2 = comp.row()
        btn_install = QPushButton("从压缩包安装…")
        btn_install.setObjectName("Ghost")
        btn_install.clicked.connect(self._install)
        r2.addWidget(btn_install)
        btn_dir = QPushButton("打开安装目录")
        btn_dir.setObjectName("Ghost")
        btn_dir.clicked.connect(self._open_dir)
        r2.addWidget(btn_dir)
        r2.addStretch(1)

        comp.add(Hint(
            "第一次使用先「启动组件」，它会联网准备 QQ 运行环境（约 300MB，需要等待）。\n"
            "已有组件压缩包时可用「从压缩包安装…」离线安装。"
        ))
        middle.addWidget(comp, 1)

        # ---------------- 右：二维码
        qr = Card()
        qr.add(SectionTitle("扫码登录"))
        self.qr_label = QLabel("尚未获取二维码")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setMinimumSize(220, 220)
        self.qr_label.setStyleSheet(
            "QLabel { background: rgba(255,255,255,0.94); color: #333;"
            " border-radius: 12px; }"
        )
        qr.add(self.qr_label, 1)

        self.qr_hint = Hint("点「① 获取/刷新二维码」，用手机 QQ（建议小号）扫码。")
        self.qr_hint.setWordWrap(True)
        qr.add(self.qr_hint)

        q1 = qr.row()
        self.btn_qr = QPushButton("① 获取/刷新二维码")
        self.btn_qr.setObjectName("Primary")
        self.btn_qr.clicked.connect(self._fetch_qr)
        q1.addWidget(self.btn_qr)
        btn_probe = QPushButton("② 我已扫码，检查登录状态")
        btn_probe.clicked.connect(self._probe)
        q1.addWidget(btn_probe)

        q2 = qr.row()
        btn_web = QPushButton("🌐  打开 NapCat 网页版（备用扫码入口）")
        btn_web.setObjectName("Ghost")
        btn_web.clicked.connect(self._open_webui)
        q2.addWidget(btn_web)
        btn_save_qr = QPushButton("保存二维码为图片…")
        btn_save_qr.setObjectName("Ghost")
        btn_save_qr.clicked.connect(self._save_qr)
        q2.addWidget(btn_save_qr)
        q2.addStretch(1)
        middle.addWidget(qr, 1)
        lay.addLayout(middle)

        # ---------------- 连接参数
        params = Card()
        params.add(SectionTitle("连接参数"))
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)

        g.addWidget(QLabel("OneBot API 地址"), 0, 0)
        self.api_base = QLineEdit()
        self.api_base.setPlaceholderText("http://127.0.0.1:3000")
        g.addWidget(self.api_base, 0, 1)

        g.addWidget(QLabel("WebUI 地址"), 1, 0)
        self.webui_base = QLineEdit()
        self.webui_base.setPlaceholderText("http://127.0.0.1:6099")
        g.addWidget(self.webui_base, 1, 1)

        g.addWidget(QLabel("OneBot 访问令牌"), 2, 0)
        self.onebot_token = QLineEdit()
        self.onebot_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.onebot_token.setPlaceholderText("可留空")
        g.addWidget(self.onebot_token, 2, 1)

        g.addWidget(QLabel("WebUI 令牌"), 3, 0)
        self.webui_token = QLineEdit()
        self.webui_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.webui_token.setPlaceholderText("一般无需手填，点右侧自动读取")
        g.addWidget(self.webui_token, 3, 1)
        params.body.addLayout(g)

        prow = params.row()
        btn_save_tok = QPushButton("保存令牌（加密）")
        btn_save_tok.setObjectName("Primary")
        btn_save_tok.clicked.connect(self._save_tokens)
        prow.addWidget(btn_save_tok)
        btn_auto = QPushButton("从 NapCat 配置自动读取")
        btn_auto.clicked.connect(self._discover_token)
        prow.addWidget(btn_auto)
        prow.addStretch(1)

        params.add(Hint(
            "程序**以组件自己的配置文件为准**读取 WebUI 令牌 —— 切换运行方式后旧令牌会失效，"
            "用旧值只会得到「令牌无效」并触发登录限流。所以一般不需要手填。\n"
            "两个令牌都用 Windows DPAPI 加密保存，不写进配置文件。"
        ))
        lay.addWidget(params)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 生命周期

    def on_start(self) -> None:
        cfg = self.controller.config
        self.api_base.setText(cfg.napcat.api_base or "")
        self.webui_base.setText(cfg.napcat.webui_base or "")
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

    # ------------------------------------------------------------ 组件

    def _start(self) -> None:
        try:
            ok = self.controller.start_napcat()
            self.toast.show("组件启动中…" if ok else "启动未生效，请看状态",
                            "success" if ok else "warning")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"启动失败：{exc}", "error")

    def _stop(self) -> None:
        try:
            self.controller.stop_napcat()
            self.toast.show("已停止组件", "info")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"停止失败：{type(exc).__name__}", "error")

    def _install(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "选择 NapCat 组件压缩包", "", "压缩包 (*.zip)")
        if not path:
            return
        try:
            ok = self.controller.install_napcat_from_zip(path)
            self.toast.show("组件安装完成" if ok else "安装失败，请看日志",
                            "success" if ok else "error")
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"安装失败：{type(exc).__name__}: {exc}", "error")

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

    # ------------------------------------------------------------ 登录

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

    def _open_webui(self) -> None:
        import webbrowser

        try:
            url = self.controller.napcat_webui_url() or self.webui_base.text().strip()
            webbrowser.open(url)
            self.toast.show("已用浏览器打开 NapCat 网页版", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"打开失败：{type(exc).__name__}", "error")

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

    # ------------------------------------------------------------ 令牌

    def _save_tokens(self) -> None:
        cfg = self.controller.config
        cfg.napcat.api_base = self.api_base.text().strip()
        cfg.napcat.webui_base = self.webui_base.text().strip()
        try:
            problems = self.controller.save_settings()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"保存配置失败：{type(exc).__name__}", "error")
            return

        saved = []
        try:
            from ..secrets import KEY_NAPCAT_WEBUI_TOKEN, KEY_QQ_ONEBOT_TOKEN

            store = self.controller.secrets
            if store is not None:
                if self.webui_token.text().strip():
                    store.set(KEY_NAPCAT_WEBUI_TOKEN, self.webui_token.text().strip())
                    saved.append("WebUI")
                if self.onebot_token.text().strip():
                    store.set(KEY_QQ_ONEBOT_TOKEN, self.onebot_token.text().strip())
                    saved.append("OneBot")
        except Exception as exc:  # noqa: BLE001
            log.debug("写令牌失败：%s", exc)

        self.webui_token.clear()
        self.onebot_token.clear()
        msg = "地址已保存" + (f"，令牌已加密保存（{'+'.join(saved)}）" if saved else "（未填新令牌）")
        if problems:
            msg += "；注意：" + "；".join(problems[:1])
        self.toast.show(msg, "success" if not problems else "warning")

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
                    f"{getattr(event, 'message', '')}\n{hint}\n可以再点一次重试。".strip()
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
        self.qr_label.setPixmap(pix.scaled(
            side, side, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
