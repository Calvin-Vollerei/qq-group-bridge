"""OpenList 进程管理 —— 本地自动关联启动。

**为什么需要它**

搬运工通过 WebDAV 把文件交给 OpenList，再由 OpenList 上传到百度网盘。
OpenList 是一个**独立进程，不会自己启动**：用户忘了开，搬运工就连不上网盘，
而界面上只会显示一句「连接失败」——极难联想到"是那个中转程序没开"。

所以这里做三件事：

1. 程序启动时**按需把它拉起来**（只在目标确实是本机时才做，见
   :meth:`is_local_target`）；
2. 程序退出时把它关掉，但**只关自己启动的那一个** —— 用户手动开着的
   实例绝不碰（那是他自己的，可能有别的用途）；
3. 把状态如实报给界面，包括"我不知道它装在哪"这种情况。

**不做的事**：不下载、不安装、不改 OpenList 的配置。凭据也一律不碰。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .config import UploadConfig
from .errors import QgbError

__all__ = ["OpenListManager", "OpenListStatus", "openlist_candidates"]

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"

#: 视为"本机"的主机名（只有这些才值得启动本地 OpenList）
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}

#: 可执行文件候选名
_EXE_NAMES = ("openlist.exe", "openlist")

#: 启动后等待端口就绪的默认秒数
DEFAULT_START_TIMEOUT = 25.0


@dataclass(slots=True)
class OpenListStatus:
    """给界面看的 OpenList 状态（**不含任何凭据**）。"""

    installed: bool = False
    running: bool = False
    #: 是否由本程序启动（决定退出时该不该由我们关掉）
    managed: bool = False
    install_dir: str = ""
    exe: str = ""
    host: str = "127.0.0.1"
    port: int = 5244
    webui_url: str = ""
    webdav_url: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed,
            "running": self.running,
            "managed": self.managed,
            "install_dir": self.install_dir,
            "exe": self.exe,
            "host": self.host,
            "port": self.port,
            "webui_url": self.webui_url,
            "webdav_url": self.webdav_url,
            "note": self.note,
        }


def openlist_candidates(app_base: Path, data_dir: Path) -> list[Path]:
    """按可能性排列的 OpenList 安装目录候选。

    覆盖真实的几种摆放方式：

    * 用户显式配置（优先级最高，由 :meth:`OpenListManager.install_dir` 处理）
    * 发布目录旁边（``<程序目录>/OpenList``）—— 我们推荐的随包摆放
    * 发布目录的**同级**（``<程序目录>/../OpenList``）—— 开发期常见
    * 数据目录下（``data/openlist``）
    """
    seen: list[Path] = []
    for candidate in (
        app_base / "OpenList",
        app_base / "openlist",
        app_base.parent / "OpenList",
        app_base.parent / "openlist",
        # 开发/便携布局：项目根/dist/OpenList；发布目录的父级也是 dist，
        # 所以这条同时覆盖"源码运行"和"发布目录与 OpenList 并排"两种情形
        app_base / "dist" / "OpenList",
        app_base / "dist" / "openlist",
        data_dir / "openlist",
        data_dir / "OpenList",
    ):
        if candidate not in seen:
            seen.append(candidate)
    return seen


class OpenListManager:
    """管理本机 OpenList 进程（启动 / 停止 / 状态）。"""

    def __init__(
        self,
        cfg: UploadConfig,
        *,
        app_base: Path,
        data_dir: Path,
        log_dir: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.app_base = Path(app_base)
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir) if log_dir else self.data_dir / "logs"
        self._proc: subprocess.Popen | None = None
        self._started_by_us = False

    # -------------------------------------------------- 目标解析

    def _webdav_target(self) -> tuple[str, int]:
        """从 WebDAV 地址解出 (host, port)。"""
        url = (self.cfg.webdav_url or "").strip()
        if not url:
            return "127.0.0.1", 5244
        parsed = urlparse(url if "://" in url else f"http://{url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 5244)
        return host, int(port)

    def is_local_target(self) -> bool:
        """WebDAV 目标是否指向本机。

        **这是自动启停的总开关。** 未来把 OpenList 放到云服务器上时，
        目标就不再是本机了 —— 那时绝不该去启动本地进程，
        否则会白白拉起一个用不上的实例，还会在退出时误关。
        """
        host, _port = self._webdav_target()
        return host.lower() in _LOCAL_HOSTS

    @property
    def host(self) -> str:
        return self._webdav_target()[0]

    @property
    def port(self) -> int:
        return self._webdav_target()[1]

    def webui_url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def webdav_url(self) -> str:
        return (self.cfg.webdav_url or f"http://{self.host}:{self.port}/dav").rstrip("/")

    # -------------------------------------------------- 安装位置

    @property
    def install_dir(self) -> Path:
        """OpenList 所在目录。

        先看配置；否则按 :func:`openlist_candidates` 逐个探测，**必须真的
        含可执行文件才算命中** —— 否则会指着空目录说"已安装"。
        """
        explicit = (getattr(self.cfg, "openlist_dir", "") or "").strip()
        if explicit:
            return Path(explicit).expanduser()

        for candidate in openlist_candidates(self.app_base, self.data_dir):
            for name in _EXE_NAMES:
                try:
                    if (candidate / name).is_file():
                        return candidate
                except OSError:
                    continue
        # 都没找到时返回首选位置，便于界面提示"放这里"
        return openlist_candidates(self.app_base, self.data_dir)[0]

    def exe_path(self) -> Path | None:
        for name in _EXE_NAMES:
            candidate = self.install_dir / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def is_installed(self) -> bool:
        return self.exe_path() is not None

    # -------------------------------------------------- 运行状态

    def is_running(self, timeout: float = 0.8) -> bool:
        """端口探测。

        **不用进程名判断**：机器上可能有别的同名程序，而"端口通不通"
        才是"搬运工能不能用"的真实判据。
        """
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except (OSError, ValueError):
            return False

    def status(self) -> OpenListStatus:
        exe = self.exe_path()
        running = self.is_running()
        st = OpenListStatus(
            installed=exe is not None,
            running=running,
            managed=self._started_by_us and running,
            install_dir=str(self.install_dir),
            exe=str(exe) if exe else "",
            host=self.host,
            port=self.port,
            webui_url=self.webui_url(),
            webdav_url=self.webdav_url(),
        )

        if not self.is_local_target():
            st.note = f"WebDAV 指向远端（{self.host}），本机不需要 OpenList"
        elif running and self._started_by_us:
            st.note = "运行中（由本程序启动，退出时会一并关闭）"
        elif running:
            st.note = "运行中（由你自己启动，本程序不会关闭它）"
        elif exe is None:
            st.note = f"未找到 openlist.exe，请放到：{self.install_dir}"
        else:
            st.note = "未运行"
        return st

    # -------------------------------------------------- 启动 / 停止

    def start(self, *, wait: float = DEFAULT_START_TIMEOUT) -> OpenListStatus:
        """启动 OpenList 并等待端口就绪。已在运行则直接返回。"""
        if self.is_running():
            return self.status()

        if not self.is_local_target():
            raise QgbError(
                f"WebDAV 目标是远端（{self.host}），不应启动本地 OpenList",
                hint="本机自动启动只在 WebDAV 指向 127.0.0.1 时才有意义。",
            )

        exe = self.exe_path()
        if exe is None:
            raise QgbError(
                "未找到 openlist.exe",
                hint=f"请把 OpenList 解压到：{self.install_dir}",
            )

        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.log_dir / "openlist.out.log"
        stderr_path = self.log_dir / "openlist.err.log"

        creationflags = 0
        if _IS_WINDOWS:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        try:
            out_fh = stdout_path.open("ab")
            err_fh = stderr_path.open("ab")
            self._proc = subprocess.Popen(
                [str(exe), "server"],
                cwd=str(self.install_dir),
                stdout=out_fh,
                stderr=err_fh,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise QgbError(
                f"启动 OpenList 失败：{exc}",
                hint="请确认文件完整，且杀毒软件没有拦截。",
            ) from exc

        self._started_by_us = True
        log.info("已启动 OpenList（pid=%s）", self._proc.pid)

        deadline = time.time() + max(0.0, wait)
        while time.time() < deadline:
            if self._proc.poll() is not None:
                # 进程自己退了 —— 多半是首次运行的配置问题或端口被占
                tail = self.last_log_tail()
                raise QgbError(
                    "OpenList 启动后立即退出",
                    hint=tail or "请查看 logs/openlist.out.log 了解原因。",
                )
            if self.is_running():
                log.info("OpenList 已就绪：%s", self.webui_url())
                return self.status()
            time.sleep(0.4)

        raise QgbError(
            f"OpenList 启动超时（{wait:.0f} 秒内端口 {self.port} 未就绪）",
            hint=f"可查看日志：{self.log_dir / 'openlist.out.log'}",
        )

    def stop(self, *, timeout: float = 12.0) -> None:
        """停止 OpenList —— **只关本程序启动的那个**。

        用户自己启动的实例绝不碰：那是他的进程，可能有别的用途，
        按进程名去杀是非常不礼貌甚至危险的行为（同机器上也可能有同名程序）。
        """
        if not self._started_by_us:
            log.info("OpenList 不是本程序启动的，跳过自动关闭")
            return
        if self._proc is None or self._proc.poll() is not None:
            # 曾经启动过但进程已退出 —— 把状态清干净，避免下次误判
            self._proc = None
            self._started_by_us = False
            return

        pid = self._proc.pid
        try:
            if _IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                self._proc.terminate()
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("停止 OpenList 时出现问题：%s", exc.__class__.__name__)

        self._proc = None
        self._started_by_us = False
        log.info("已停止 OpenList")

    # -------------------------------------------------- 辅助

    def last_log_tail(self, lines: int = 15) -> str:
        """读最近一次启动的输出（排查失败用，已脱敏）。"""
        chunks: list[str] = []
        for name in ("openlist.out.log", "openlist.err.log"):
            path = self.log_dir / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            if text:
                chunks.append("\n".join(text.splitlines()[-lines:]))

        from .redact import redact_text

        return redact_text("\n".join(chunks))

    def ensure_running(self) -> tuple[bool, str]:
        """按需启动（供程序启动时调用）。返回 ``(是否已就绪, 说明)``。

        **不抛异常** —— 网盘没起来不该阻止程序启动，用户仍然可以配置、
        看日志、管理 QQ 组件。
        """
        if not self.is_local_target():
            return True, f"WebDAV 指向远端 {self.host}，跳过本地 OpenList"
        if self.is_running():
            return True, "OpenList 已在运行"
        if not self.is_installed():
            return False, f"未找到 OpenList（应在 {self.install_dir}）"

        try:
            self.start()
        except QgbError as exc:
            return False, exc.message
        return True, "已自动启动 OpenList"
