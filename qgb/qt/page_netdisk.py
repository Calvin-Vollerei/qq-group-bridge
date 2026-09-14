"""网盘与凭据页：上传目标、凭据管理、OpenList 托管。

补齐旧 Tk 版功能（对照 qgb/gui/tab_netdisk.py）：
  上传方式（WebDAV / 本地目录）/ 网盘目标目录 / 按群号分子目录 /
  上传后回读校验 / 随程序自动启停 OpenList / 群目录命名
  保存凭据 / 清除凭据 / 本机凭据一览 / 清除本机全部凭据
  测试连接 / 打开网盘看上传结果
  OpenList：状态 / 启动 / 停止（仅限本程序启动的）/ 打开管理后台
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .shell import Page
from .widgets import Card, Hint, SectionTitle, StatusBadge

log = logging.getLogger(__name__)


class NetdiskPage(Page):
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

        # ---------------- 上传目标
        conn = Card()
        head = conn.row()
        head.addWidget(SectionTitle("上传目标"))
        head.addStretch(1)
        self.badge = StatusBadge("未测试")
        head.addWidget(self.badge)

        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        # 宽度自适应：标签列固定，输入列拉伸填满窗口
        g.setColumnStretch(0, 0)
        g.setColumnStretch(1, 1)

        g.addWidget(QLabel("上传方式"), 0, 0)
        self.adapter = QComboBox()
        self.adapter.addItem("OpenList（WebDAV 中转）", "webdav")
        self.adapter.addItem("本地/已挂载目录", "local")
        g.addWidget(self.adapter, 0, 1)

        g.addWidget(QLabel("WebDAV 地址"), 1, 0)
        self.url = QLineEdit()
        self.url.setMinimumWidth(240)
        self.url.setPlaceholderText("http://127.0.0.1:5244/dav")
        g.addWidget(self.url, 1, 1)

        g.addWidget(QLabel("用户名"), 2, 0)
        self.user = QLineEdit()
        g.addWidget(self.user, 2, 1)

        g.addWidget(QLabel("密码"), 3, 0)
        self.pwd = QLineEdit()
        self.pwd.setEchoMode(QLineEdit.EchoMode.Password)
        g.addWidget(self.pwd, 3, 1)

        g.addWidget(QLabel("网盘目标目录"), 4, 0)
        self.root_dir = QLineEdit()
        self.root_dir.setPlaceholderText("/baidu/QQ群备份")
        g.addWidget(self.root_dir, 4, 1)

        g.addWidget(QLabel("本地目录（仅本地方式）"), 5, 0)
        self.local_root = QLineEdit()
        self.local_root.setPlaceholderText(r"D:\资料\学习资料")
        g.addWidget(self.local_root, 5, 1)

        g.addWidget(QLabel("群目录命名"), 6, 0)
        self.folder_style = QComboBox()
        for key, label in (("id", "群号"), ("name", "群名"), ("id_name", "群号_群名")):
            self.folder_style.addItem(label, key)
        g.addWidget(self.folder_style, 6, 1)
        conn.body.addLayout(g)

        switches = conn.row()
        self.split_by_group = QCheckBox("按群号分子目录")
        switches.addWidget(self.split_by_group)
        self.verify = QCheckBox("上传后回读校验大小")
        switches.addWidget(self.verify)
        self.manage_openlist = QCheckBox("随程序自动启停 OpenList")
        switches.addWidget(self.manage_openlist)
        switches.addStretch(1)

        brow = conn.row()
        btn_save = QPushButton("💾  保存凭据")
        btn_save.setObjectName("Primary")
        btn_save.clicked.connect(self._save)
        brow.addWidget(btn_save)
        btn_test = QPushButton("🔌  测试连接")
        btn_test.clicked.connect(self._test)
        brow.addWidget(btn_test)
        btn_open = QPushButton("打开网盘看上传结果")
        btn_open.setObjectName("Ghost")
        btn_open.clicked.connect(lambda: self._open_netdisk("baidu"))
        brow.addWidget(btn_open)
        btn_clear = QPushButton("清除凭据")
        btn_clear.setObjectName("Danger")
        btn_clear.clicked.connect(self._clear_creds)
        brow.addWidget(btn_clear)
        brow.addStretch(1)

        conn.add(Hint(
            "凭据用 Windows DPAPI 加密保存在本机（只有当前 Windows 用户能解开），"
            "不会写进配置文件、也不会随分发包外传。"
        ))
        lay.addWidget(conn)

        # ---------------- OpenList
        ol = Card()
        ohead = ol.row()
        ohead.addWidget(SectionTitle("OpenList（WebDAV 中转）"))
        ohead.addStretch(1)
        self.ol_badge = StatusBadge("未知")
        ohead.addWidget(self.ol_badge)
        self.ol_info = Hint("")
        ol.add(self.ol_info)

        orow = ol.row()
        for text, slot, style in (
            ("🔄  刷新状态", self._refresh_openlist, "ghost"),
            ("▶  启动 OpenList", self._start_openlist, "primary"),
            ("⏹  停止（仅限本程序启动的）", self._stop_openlist, "ghost"),
            ("🌐  打开管理后台", self._open_openlist, "ghost"),
        ):
            btn = QPushButton(text)
            if style == "primary":
                btn.setObjectName("Primary")
            btn.clicked.connect(slot)
            orow.addWidget(btn)
        orow.addStretch(1)

        ol.add(Hint(
            "本程序通过 OpenList 的 WebDAV 接口写文件。若 OpenList 不在这台机器上，"
            "把上面的地址改成它的局域网/公网地址即可，无需在这里启停。\n"
            "百度网盘的授权（refresh token）比较绕 —— 交付包里有"
            "《百度网盘授权怎么做.txt》可照着做。"
        ))
        lay.addWidget(ol)

        # ---------------- 本机凭据
        creds = Card()
        creds.add(SectionTitle("本机保存的凭据"))
        self.cred_list = QListWidget()
        self.cred_list.setMinimumHeight(90)
        creds.add(self.cred_list)
        crow = creds.row()
        btn_reload_creds = QPushButton("🔄  重新检测")
        btn_reload_creds.setObjectName("Ghost")
        btn_reload_creds.clicked.connect(self._reload_creds)
        crow.addWidget(btn_reload_creds)
        btn_wipe = QPushButton("🗑  清除本机全部凭据")
        btn_wipe.setObjectName("Danger")
        btn_wipe.clicked.connect(self._wipe_creds)
        crow.addWidget(btn_wipe)
        crow.addStretch(1)
        creds.add(Hint("这里只显示有哪些凭据、是否已保存，不会显示内容。"))
        lay.addWidget(creds)

        lay.addStretch(1)
        area.setWidget(root)
        return area

    # ------------------------------------------------------------ 载入

    def on_start(self) -> None:
        cfg = self.controller.config
        idx = self.adapter.findData(cfg.upload.adapter or "webdav")
        if idx >= 0:
            self.adapter.setCurrentIndex(idx)
        self.url.setText(cfg.upload.webdav_url or "")
        self.root_dir.setText(cfg.upload.remote_root or "")
        self.local_root.setText(cfg.upload.local_root or "")
        fidx = self.folder_style.findData(cfg.upload.folder_style or "id")
        if fidx >= 0:
            self.folder_style.setCurrentIndex(fidx)
        self.split_by_group.setChecked(bool(cfg.upload.split_by_group))
        self.verify.setChecked(bool(cfg.upload.verify_after_upload))
        self.manage_openlist.setChecked(bool(cfg.upload.manage_openlist))

        try:
            from ..secrets import KEY_NETDISK_WEBDAV_USERNAME

            if self.controller.secrets is not None:
                self.user.setText(
                    self.controller.secrets.get(KEY_NETDISK_WEBDAV_USERNAME) or "")
        except Exception:  # noqa: BLE001
            pass

        self._reload_creds()
        self._refresh_openlist()

    def _reload_creds(self) -> None:
        self.cred_list.clear()
        try:
            rows = self.controller.credentials_overview() or []
        except Exception:  # noqa: BLE001
            rows = []
        if not rows:
            self.cred_list.addItem("（尚未保存任何凭据）")
            return
        for row in rows:
            key = str(row.get("key") or row.get("name") or "?")
            state = "已保存" if row.get("present") or row.get("saved") else "未保存"
            self.cred_list.addItem(f"{key}　{state}")

    # ------------------------------------------------------------ 保存 / 测试

    def _save(self) -> None:
        cfg = self.controller.config
        cfg.upload.adapter = self.adapter.currentData() or "webdav"
        cfg.upload.webdav_url = self.url.text().strip()
        cfg.upload.remote_root = self.root_dir.text().strip()
        cfg.upload.local_root = self.local_root.text().strip()
        cfg.upload.folder_style = self.folder_style.currentData() or "id"
        cfg.upload.split_by_group = self.split_by_group.isChecked()
        cfg.upload.verify_after_upload = self.verify.isChecked()
        cfg.upload.manage_openlist = self.manage_openlist.isChecked()

        try:
            self.controller.save_settings()
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"保存配置失败：{type(exc).__name__}", "error")
            return

        saved = False
        if self.user.text().strip() or self.pwd.text():
            try:
                self.controller.save_webdav_credentials(
                    self.user.text().strip(), self.pwd.text())
                saved = True
            except Exception as exc:  # noqa: BLE001
                log.debug("save_webdav_credentials 失败：%s", exc)
                saved = self._save_creds_fallback()

        if saved:
            self.pwd.clear()
        self._reload_creds()
        self.toast.show("设置已保存" + ("，凭据已加密保存" if saved else ""), "success")

    def _save_creds_fallback(self) -> bool:
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
        self._save()
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
            self.toast.show("连接失败：检查地址/用户名密码，以及 OpenList 是否在运行", "error")

    def _clear_creds(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(self, "清除凭据",
                                "将清除本机保存的 WebDAV 凭据，确定吗？") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.clear_webdav_credentials()
            self.user.clear()
            self.pwd.clear()
            self._reload_creds()
            self.toast.show("已清除 WebDAV 凭据", "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"清除失败：{type(exc).__name__}", "error")

    def _wipe_creds(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(
            self, "清除全部凭据",
            "将清除本机保存的全部凭据（网盘 + QQ 令牌）。\n清除后需要重新填写/授权。确定吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.controller.clear_all_credentials()
            self._reload_creds()
            self.toast.show("已清除本机全部凭据", "success")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"清除失败：{type(exc).__name__}", "error")

    def _open_netdisk(self, key: str = "baidu") -> None:
        try:
            self.controller.open_netdisk(key)
            self.toast.show("已用浏览器打开网盘页面", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"打开失败：{type(exc).__name__}", "error")

    # ------------------------------------------------------------ OpenList

    def _openlist(self):
        """构造 OpenList 管理器。

        ⚠️ 真实签名是 ``OpenListManager(cfg.upload, *, app_base=, data_dir=, log_dir=)``
        —— 早期我写成 ``OpenListManager(config, data_dir)``（参数个数与类型都错），
        导致这一页的 OpenList 按钮一点就抛 TypeError（表现为"OpenList 坏了"）。
        这里按真实签名构造；``app_base`` 指向程序根目录，迁移路径后自动跟着走。
        """
        from ..openlist import OpenListManager

        # controller.data_dir 在 load() 时就确定了（默认数据目录或 QGB_DATA_DIR），
        # 直接用它最稳 —— 早期我误从 qgb.paths 导入 default_data_dir（那里没有这个名字），
        # 于是整段抛 ImportError，表现仍是"OpenList 坏了"。
        app_base = Path(__file__).resolve().parent.parent.parent
        data_dir = self.controller.data_dir
        if data_dir is None:
            data_dir = app_base / "dist" / "QQ群文件搬运工" / "data"
        return OpenListManager(
            self.controller.config.upload,
            app_base=app_base,
            data_dir=Path(data_dir),
        )

    def _refresh_openlist(self) -> None:
        try:
            status = self._openlist().status()
        except Exception as exc:  # noqa: BLE001
            self.ol_badge.set_state("idle", "未知")
            self.ol_info.setText(f"（无法获取 OpenList 状态：{type(exc).__name__}: {exc}）")
            return
        running = bool(getattr(status, "running", False))
        installed = bool(getattr(status, "installed", False))
        if running:
            self.ol_badge.set_state("ok", "运行中")
        elif installed:
            self.ol_badge.set_state("warn", "已安装未运行")
        else:
            self.ol_badge.set_state("idle", "未找到")
        note = str(getattr(status, "note", "") or "")
        self.ol_info.setText(
            f"管理后台：{getattr(status, 'webui_url', '') or '—'}\n"
            f"WebDAV ：{getattr(status, 'webdav_url', '') or '—'}\n"
            f"可执行文件：{getattr(status, 'exe', '') or '—'}"
            + (f"\n状态说明：{note}" if note else "")
        )

    def _start_openlist(self) -> None:
        self.ol_badge.set_state("info", "启动中…")
        try:
            status = self._openlist().start()      # 内部会等待就绪
            ok = bool(getattr(status, "running", False))
            self.toast.show(
                "OpenList 已启动" if ok else "启动未成功，请看下方状态说明",
                "success" if ok else "warning",
            )
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"启动失败：{type(exc).__name__}: {exc}", "error")
        self._refresh_openlist()

    def _stop_openlist(self) -> None:
        try:
            self._openlist().stop()
            self.toast.show("已请求停止 OpenList（仅限本程序启动的）", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"停止失败：{type(exc).__name__}: {exc}", "error")
        self._refresh_openlist()

    def _open_openlist(self) -> None:
        import webbrowser

        try:
            status = self._openlist().status()
            url = getattr(status, "webui_url", "") or "http://127.0.0.1:5244"
            webbrowser.open(url)
            self.toast.show("已打开 OpenList 管理后台", "info")
        except Exception as exc:  # noqa: BLE001
            self.toast.show(f"打开失败：{type(exc).__name__}", "error")
