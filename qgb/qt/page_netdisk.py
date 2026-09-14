"""网盘与凭据页：WebDAV 地址、凭据、连接测试、远端目录风格。"""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QComboBox,
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


class NetdiskPage(Page):
    """网盘（WebDAV）配置与凭据。

    凭据由控制器写入 Windows DPAPI 加密库，**不落配置文件**；
    这里只负责界面与调用。
    """

    title = "网盘与凭据"

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

        # ---------------- 连接
        conn = Card()
        head = conn.row()
        head.addWidget(SectionTitle("网盘连接（WebDAV）"))
        head.addStretch(1)
        self.badge = StatusBadge("未测试")
        head.addWidget(self.badge)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        grid.addWidget(QLabel("WebDAV 地址"), 0, 0)
        self.url = QLineEdit()
        self.url.setPlaceholderText("http://127.0.0.1:5244/dav")
        grid.addWidget(self.url, 0, 1)

        grid.addWidget(QLabel("用户名"), 1, 0)
        self.user = QLineEdit()
        grid.addWidget(self.user, 1, 1)

        grid.addWidget(QLabel("密码"), 2, 0)
        self.pwd = QLineEdit()
        self.pwd.setEchoMode(QLineEdit.EchoMode.Password)
        grid.addWidget(self.pwd, 2, 1)

        grid.addWidget(QLabel("远端根目录"), 3, 0)
        self.root_dir = QLineEdit()
        self.root_dir.setPlaceholderText("/baidu/QQ群备份")
        grid.addWidget(self.root_dir, 3, 1)

        grid.addWidget(QLabel("子目录命名"), 4, 0)
        self.folder_style = QComboBox()
        for key, label in (("id", "群号"), ("name", "群名"), ("id_name", "群号_群名")):
            self.folder_style.addItem(label, key)
        grid.addWidget(self.folder_style, 4, 1)
        conn.body.addLayout(grid)

        row = conn.row()
        btn_save = QPushButton("保存凭据")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save_credentials)
        row.addWidget(btn_save)
        btn_test = QPushButton("测试连接")
        btn_test.clicked.connect(self._test)
        row.addWidget(btn_test)
        btn_open = QPushButton("打开网盘页面")
        btn_open.setObjectName("Ghost")
        btn_open.clicked.connect(self._open_netdisk)
        row.addWidget(btn_open)
        row.addStretch(1)

        conn.add(Hint(
            "凭据用 Windows DPAPI 加密保存在本机（只有当前 Windows 用户能解开），"
            "**不会**写进配置文件、也不会随分发包外传。\n"
            "WebDAV 地址默认指向本机的 OpenList；若 OpenList 不在这台机器上，"
            "改成它的局域网/公网地址即可。"
        ))
        lay.addWidget(conn)

        # ---------------- 说明
        tip = Card()
        tip.add(SectionTitle("关于 OpenList（第一次配置看这里）"))
        tip.add(Hint(
            "本程序通过 OpenList 的 WebDAV 接口把文件写进网盘。\n\n"
            "1. 启动 OpenList（默认 http://127.0.0.1:5244），用管理员账号登录；\n"
            "2. 在「存储」里添加你的网盘（百度网盘需要 refresh token）；\n"
            "3. 确认挂载路径与上面的「远端根目录」一致（例如 /baidu 对应 /baidu/QQ群备份）；\n"
            "4. 把 OpenList 的**用户名/密码**填到上面并保存。\n\n"
            "网盘授权那一步（取 refresh token）比较绕，交付包里有一份"
            "《百度网盘授权怎么做.txt》可以照着做。"
        ))
        lay.addWidget(tip)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 生命周期

    def on_start(self) -> None:
        self._reload()

    def _reload(self) -> None:
        cfg = self.controller.config
        self.url.setText(cfg.upload.webdav_url or "")
        self.root_dir.setText(cfg.upload.remote_root or "")
        idx = self.folder_style.findData(cfg.upload.folder_style or "id")
        if idx >= 0:
            self.folder_style.setCurrentIndex(idx)
        # 用户名回填（密码不回显）
        try:
            from ..secrets import KEY_NETDISK_WEBDAV_USERNAME

            if self.controller.secrets is not None:
                self.user.setText(self.controller.secrets.get(KEY_NETDISK_WEBDAV_USERNAME) or "")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ 动作

    def _save_credentials(self) -> None:
        cfg = self.controller.config
        cfg.upload.webdav_url = self.url.text().strip()
        cfg.upload.remote_root = self.root_dir.text().strip()
        cfg.upload.folder_style = self.folder_style.currentData() or "id"
        try:
            self.controller.save_settings()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"保存配置失败：{type(exc).__name__}", "error")
            return

        # 真实签名是 (username, password)；URL 属于配置，已由 save_settings 落盘
        try:
            self.controller.save_webdav_credentials(
                self.user.text().strip(), self.pwd.text()
            )
            ok = True
        except Exception as exc:  # noqa: BLE001
            log.debug("save_webdav_credentials 失败，退回直接写凭据库：%s", exc)
            ok = self._save_credentials_fallback()

        if ok:
            self.pwd.clear()
            self.toast.show("配置与凭据已保存（密码不回显，已加密存储）", "success")
        else:
            self.toast.show("配置已保存，但凭据写入失败", "warning")

    def _save_credentials_fallback(self) -> bool:
        """控制器没有专用方法时，直接写凭据库（功能等价）。"""
        try:
            from ..secrets import (
                KEY_NETDISK_WEBDAV_PASSWORD,
                KEY_NETDISK_WEBDAV_USERNAME,
            )

            store = self.controller.secrets
            if store is None:
                return False
            store.set(KEY_NETDISK_WEBDAV_USERNAME, self.user.text().strip())
            store.set(KEY_NETDISK_WEBDAV_PASSWORD, self.pwd.text())
            return True
        except Exception:  # noqa: BLE001
            return False

    def _test(self) -> None:
        self.badge.set_state("info", "测试中…")
        self._save_credentials()
        try:
            ok = self.controller.test_netdisk()
        except Exception as exc:  # noqa: BLE001
            self.badge.set_state("error", "测试失败")
            self.toast.show(f"测试失败：{type(exc).__name__}", "error")
            return
        if ok:
            self.badge.set_state("ok", "连接正常")
            self.toast.show("网盘连接正常", "success")
        else:
            self.badge.set_state("error", "连接失败")
            self.toast.show("连接失败：请检查地址、用户名密码、OpenList 是否在运行", "error")

    def _open_netdisk(self) -> None:
        try:
            self.controller.open_netdisk("baidu")
            self.toast.show("已用浏览器打开网盘页面", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"打开失败：{type(exc).__name__}", "error")
