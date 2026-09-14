"""窗口关闭行为 + 系统托盘。

用户需求：点关闭时弹窗询问「直接关闭 / 缩小到托盘」，并在设置里能改默认行为。

设计要点：
  · 托盘图标复用应用图标（qgb/assets/icon.ico），双击还原窗口；
  · ``close_action`` 三态：ask（每次问）/ tray（直接进托盘）/ quit（直接退出）；
  · **弹窗里有「记住我的选择」**，勾了就写回配置，下次不再问 ——
    否则每次关闭都弹窗会很烦；
  · 托盘菜单提供："显示主界面 / 立即刷新 / 退出程序"。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
)

log = logging.getLogger(__name__)


def icon_path() -> Path:
    """应用图标路径（打包后位于 _MEIPASS 或 qgb/assets）。"""
    here = Path(__file__).resolve().parent.parent / "assets"
    for name in ("icon.ico", "icon.png"):
        cand = here / name
        if cand.is_file():
            return cand
    return here / "icon.png"


def app_icon() -> QIcon:
    """QIcon 对象（窗口/托盘/任务栏共用）。"""
    p = icon_path()
    return QIcon(str(p)) if p.is_file() else QIcon()


class CloseDialog(QDialog):
    """关闭时询问：直接关闭还是缩小到托盘。"""

    QUIT = 1
    TRAY = 2

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("关闭窗口")
        self.setWindowIcon(app_icon())
        self.choice = 0                     # 0 = 取消

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        msg = QLabel("要关闭程序，还是缩小到托盘继续在后台搬运？")
        msg.setWordWrap(True)
        lay.addWidget(msg)

        tip = QLabel(
            "缩小到托盘：程序会继续在后台监控与搬运，点托盘图标可随时恢复界面。\n"
            "直接关闭：停止搬运并退出（下次打开再从队列继续）。"
        )
        tip.setObjectName("Hint")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.remember = QCheckBox("记住我的选择（以后不再询问）")
        lay.addWidget(self.remember)

        btns = QDialogButtonBox()
        btn_tray = btns.addButton("缩小到托盘", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_quit = btns.addButton("直接关闭", QDialogButtonBox.ButtonRole.DestructiveRole)
        btns.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_tray.setObjectName("Primary")
        btn_tray.clicked.connect(self._tray)
        btn_quit.clicked.connect(self._quit)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _tray(self) -> None:
        self.choice = self.TRAY
        self.accept()

    def _quit(self) -> None:
        self.choice = self.QUIT
        self.accept()


class TrayController(QObject):
    """系统托盘：图标 + 菜单 + 关闭行为。"""

    def __init__(self, shell) -> None:
        super().__init__(shell)
        self.shell = shell
        self.tray: QSystemTrayIcon | None = None
        self._build()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.info("系统不支持托盘图标，关闭行为按「直接退出」处理")
            return

        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("QQ群文件搬运工")

        menu = QMenu()
        act_show = QAction("显示主界面", menu)
        act_show.triggered.connect(self.restore)
        menu.addAction(act_show)

        act_once = QAction("立即刷新（拉取新文件）", menu)
        act_once.triggered.connect(self._run_once)
        menu.addAction(act_once)

        menu.addSeparator()
        act_quit = QAction("退出程序", menu)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)
        self.tray.show()

    # ------------------------------------------------------------ 行为

    def _on_activated(self, reason) -> None:
        # 双击 / 单击图标都还原窗口（不同 Windows 版本行为不一致，两种都接）
        if reason in (QSystemTrayIcon.ActivationReason.DoubleClick,
                      QSystemTrayIcon.ActivationReason.Trigger):
            self.restore()

    def restore(self) -> None:
        """从托盘还原窗口。"""
        self.shell.showNormal()
        self.shell.raise_()
        self.shell.activateWindow()

    def hide_to_tray(self) -> None:
        """缩小到托盘（首次提示一次，免得用户以为程序没了）。"""
        self.shell.hide()
        if self.tray is not None and not getattr(self, "_notified", False):
            self.tray.showMessage(
                "仍在后台运行",
                "程序已缩小到托盘，会继续监控与搬运。\n双击托盘图标可恢复界面。",
                QSystemTrayIcon.MessageType.Information,
                5000,
            )
            self._notified = True

    def quit_app(self) -> None:
        """真正退出。"""
        if self.tray is not None:
            self.tray.hide()
        self.shell.force_close = True
        self.shell.close()
        QApplication.quit()

    def _run_once(self) -> None:
        try:
            self.shell.controller.run_once_now()
        except Exception:  # noqa: BLE001
            log.debug("托盘触发刷新失败", exc_info=True)

    # ------------------------------------------------------------ 关闭决策

    def handle_close(self) -> bool:
        """窗口关闭时的决策。返回 True 表示"放行关闭（退出）"。"""
        ui = getattr(getattr(self.shell.controller, "config", None), "ui", None)
        action = str(getattr(ui, "close_action", "ask") or "ask")
        tray_ok = self.tray is not None

        # 没有托盘可用时，只能直接退出
        if not tray_ok:
            return True
        if action == "quit":
            return True
        if action == "tray":
            self.hide_to_tray()
            return False

        # ask：弹窗询问
        dlg = CloseDialog(self.shell)
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.choice == 0:
            return False                    # 取消 → 什么都不做
        if dlg.remember.isChecked() and ui is not None:
            try:
                ui.close_action = "tray" if dlg.choice == dlg.TRAY else "quit"
                self.shell.controller.save_settings()
            except Exception:  # noqa: BLE001
                log.debug("记住关闭选择失败", exc_info=True)
        if dlg.choice == dlg.TRAY:
            self.hide_to_tray()
            return False
        return True
