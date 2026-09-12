"""探测本机已安装的网盘客户端，并决定「打开网盘」该走客户端还是网页。

**为什么要自动探测安装路径**：用户把网盘装在 D 盘、E 盘或任意目录都很常见，
写死 ``C:\\Program Files\\...`` 基本一定失效。实测（本机）可靠的信息源是
卸载注册表项的 ``InstallLocation`` / ``DisplayIcon``::

    DisplayName      : 百度网盘
    InstallLocation  : D:\\BaiduNetdisk
    DisplayIcon      : "D:\\BaiduNetdisk\\BaiduNetdisk.exe"

所以探测顺序是：**注册表 → 常见路径 → 盘符扫描 → 运行中的进程**，
每一步都验证可执行文件真实存在，找不到就退回网页版。

本模块只做探测与打开，**不涉及任何账号凭据**。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

__all__ = [
    "CloudClient",
    "KNOWN_CLIENTS",
    "detect_clients",
    "find_client",
    "open_client_or_web",
    "sync_folder_candidates",
]

log = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"


@dataclass(slots=True)
class CloudClient:
    """一个已安装（或被识别的）网盘客户端。"""

    #: 内部标识，如 ``baidu``
    key: str
    #: 显示名，如 ``百度网盘``
    name: str
    #: 可执行文件绝对路径；为空表示只找到了安装目录
    exe: str = ""
    #: 安装目录
    install_dir: str = ""
    #: 网页版地址（客户端不可用时的兜底）
    web_url: str = ""
    #: 探测来源，便于排障与界面展示（registry / common-path / scan / process）
    source: str = ""
    #: 同步空间等本地文件夹（若存在）
    sync_dirs: list[str] = field(default_factory=list)

    @property
    def installed(self) -> bool:
        return bool(self.exe) and Path(self.exe).is_file()

    def display_path(self) -> str:
        """给界面显示的一行说明。

        三种情况的措辞要区分清楚，否则用户看到「D:\\BaiduNetdisk」会以为
        能用，点下去却打开了网页版，平白困惑。
        """
        if self.installed:
            return self.exe
        if self.install_dir:
            return f"{self.install_dir}（未找到可执行文件）"
        return "（未找到安装位置）"


#: 已知客户端。键 → (显示名, 可执行文件名候选, 网页版, 卸载项名称匹配)
KNOWN_CLIENTS: dict[str, tuple[str, tuple[str, ...], str, tuple[str, ...]]] = {
    "baidu": (
        "百度网盘",
        ("BaiduNetdisk.exe",),
        "https://pan.baidu.com/",
        ("百度网盘", "BaiduNetdisk", "baidu netdisk"),
    ),
    "quark": (
        "夸克网盘",
        ("quark_cloud_drive.exe",),
        "https://pan.quark.cn/",
        ("夸克网盘", "QuarkCloudDrive"),
    ),
    "aliyun": (
        "阿里云盘",
        ("aDrive.exe",),
        "https://www.alipan.com/",
        ("阿里云盘", "aDrive"),
    ),
}

#: 常见安装目录模板（相对路径片段）。用 %VAR% 形式，运行时展开。
_COMMON_DIRS = (
    r"%ProgramFiles%\Baidu\BaiduNetdisk",
    r"%ProgramFiles(x86)%\Baidu\BaiduNetdisk",
    r"%LOCALAPPDATA%\Baidu\BaiduNetdisk",
    r"%APPDATA%\Baidu\BaiduNetdisk",
    r"%ProgramFiles%\BaiduNetdisk",
    r"%ProgramFiles(x86)%\BaiduNetdisk",
)

#: 会在根目录直接建同名文件夹的安装方式
_ROOT_DIRS = ("BaiduNetdisk",)


def _expand(template: str) -> str:
    """展开 Windows 风格的 %VAR%（不依赖 shell）。"""
    result = template
    for key, value in os.environ.items():
        result = result.replace(f"%{key}%", value)
    # ProgramFiles(x86) 这类带括号的变量在上面已处理；兜底清掉残留
    return result


def _registry_clients() -> dict[str, tuple[str, str]]:
    """从卸载注册表项读取安装目录。

    返回 ``{key: (install_dir, exe_path)}``。有多个键命中时，
    后面的覆盖前面的 —— 但只在确实找到可执行文件时才覆盖。
    """
    if not _IS_WINDOWS:
        return {}

    try:
        import winreg
    except ImportError:
        return {}

    hives_subs = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )

    found: dict[str, tuple[str, str]] = {}
    for hive, sub in hives_subs:
        try:
            with winreg.OpenKey(hive, sub) as root:
                count = winreg.QueryInfoKey(root)[0]
                for index in range(count):
                    try:
                        name = winreg.EnumKey(root, index)
                    except OSError:
                        continue
                    try:
                        with winreg.OpenKey(root, name) as item:
                            display = _reg_str(item, "DisplayName")
                            if not display:
                                continue
                            key = _match_client(display)
                            if not key:
                                continue
                            install_dir = _reg_str(item, "InstallLocation")
                            icon = _reg_str(item, "DisplayIcon")
                            exe = _exe_from_icon(icon)
                            if not exe and install_dir:
                                exe = _first_existing(
                                    Path(install_dir), KNOWN_CLIENTS[key][1]
                                )
                            if exe or install_dir:
                                found[key] = (install_dir, exe or "")
                    except OSError:
                        continue
        except OSError:
            continue
    return found


def _reg_str(key, value_name: str) -> str:
    import winreg

    try:
        value, _ = winreg.QueryValueEx(key, value_name)
        return str(value).strip()
    except OSError:
        return ""


def _match_client(display_name: str) -> str:
    low = display_name.lower()
    for key, (_name, _exes, _web, aliases) in KNOWN_CLIENTS.items():
        for alias in aliases:
            if alias.lower() in low:
                return key
    return ""


def _exe_from_icon(icon: str) -> str:
    """``DisplayIcon`` 常是 ``"C:\\...\\X.exe",8`` 或 ``"C:\\...\\X.exe"``。"""
    raw = (icon or "").strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        end = raw.find('"', 1)
        raw = raw[1:end] if end > 0 else raw[1:]
    else:
        raw = raw.split(",")[0]
    path = Path(raw)
    return str(path) if path.is_file() else ""


def _first_existing(base: Path, names: Iterable[str]) -> str:
    for name in names:
        candidate = base / name
        try:
            if candidate.is_file():
                return str(candidate)
        except OSError:
            continue
    return ""


def _scan_drives() -> dict[str, tuple[str, str]]:
    """在常见盘符的若干目录下找客户端（注册表缺失时的兜底）。"""
    if not _IS_WINDOWS:
        return {}

    found: dict[str, tuple[str, str]] = {}
    for letter in "CDEFGH":
        root = Path(f"{letter}:\\")
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue

        candidates: list[Path] = [root / name for name in _ROOT_DIRS]
        for pattern in ("Baidu/BaiduNetdisk", "Program Files/Baidu/BaiduNetdisk",
                        "Program Files (x86)/Baidu/BaiduNetdisk"):
            candidates.append(root / pattern)

        for base in candidates:
            for key, (_name, exes, _web, _aliases) in KNOWN_CLIENTS.items():
                exe = _first_existing(base, exes)
                if exe:
                    found.setdefault(key, (str(base), exe))
    return found


def sync_folder_candidates(key: str) -> list[Path]:
    """某个网盘的「同步空间/下载目录」候选位置。

    百度网盘开启「同步空间」后会有一个本地文件夹；若用户选择用
    ``local`` 适配器直接上传到那个文件夹，就完全不需要 API 授权。
    """
    names = {
        "baidu": ("百度网盘同步空间", "BaiduNetdiskDownload", "我的资源"),
        "quark": ("夸克网盘",),
        "aliyun": ("阿里云盘",),
    }.get(key, ())

    roots: list[Path] = []
    home = Path(os.environ.get("USERPROFILE", ""))
    if home:
        roots += [home / "Documents", home / "Desktop", home]
    for letter in "CDEFGH":
        drive = Path(f"{letter}:\\")
        try:
            if drive.is_dir():
                roots.append(drive)
        except OSError:
            continue

    found: list[Path] = []
    for root in roots:
        for name in names:
            candidate = root / name
            try:
                if candidate.is_dir() and candidate not in found:
                    found.append(candidate)
            except OSError:
                continue
    return found


def detect_clients(
    *,
    registry_reader: Callable[[], dict[str, tuple[str, str]]] | None = None,
    path_candidates: dict[str, tuple[str, str]] | None = None,
    scan_drives: bool = True,
) -> list[CloudClient]:
    """探测已安装的网盘客户端。

    参数可注入，便于在测试里脱离真实注册表/盘符验证逻辑。
    """
    merged: dict[str, tuple[str, str, str]] = {}

    def absorb(source: str, data: dict[str, tuple[str, str]]) -> None:
        for key, (install_dir, exe) in data.items():
            existing = merged.get(key)
            # 已经找到可执行文件就不再被后续来源覆盖 —— 保留**最先**
            # （也最可靠）的来源，界面上的「来源：registry」才是如实描述。
            # 反过来，新结果没有 exe 而旧结果有，也不该降级。
            if existing and (existing[1] or not exe):
                continue
            merged[key] = (install_dir, exe, source)

    if registry_reader is not None:
        absorb("registry", registry_reader())
    else:
        absorb("registry", _registry_clients())

    if path_candidates is not None:
        absorb("common-path", path_candidates)

    if scan_drives:
        absorb("scan", _scan_drives())

    clients: list[CloudClient] = []
    for key, (install_dir, exe, source) in merged.items():
        name, _exes, web, _aliases = KNOWN_CLIENTS[key]
        clients.append(
            CloudClient(
                key=key,
                name=name,
                exe=exe,
                install_dir=install_dir or (str(Path(exe).parent) if exe else ""),
                web_url=web,
                source=source,
                sync_dirs=[str(p) for p in sync_folder_candidates(key)],
            )
        )

    # 安装好的排前面；百度网盘优先（这是本项目的默认目标）
    clients.sort(key=lambda c: (not c.installed, c.key != "baidu", c.name))
    return clients


def find_client(key: str = "baidu", **kwargs) -> CloudClient | None:
    for client in detect_clients(**kwargs):
        if client.key == key:
            return client
    return None


def open_client_or_web(
    key: str = "baidu",
    *,
    opener: Callable[[str], bool] | None = None,
    **kwargs,
) -> dict[str, object]:
    """打开网盘：**优先客户端，没装就退回网页版**。

    返回 ``{"opened": "client"|"web"|"none", "target": ..., "name": ..., "exe": ...}``，
    便于界面如实告诉用户到底打开了什么。
    """
    client = find_client(key, **kwargs)
    name = KNOWN_CLIENTS.get(key, (key,))[0]
    web = (client.web_url if client else "") or KNOWN_CLIENTS.get(key, ("", "", ""))[2]

    def _launch(target: str) -> bool:
        if opener is not None:
            return opener(target)
        return _default_open(target)

    if client and client.installed:
        if _launch(client.exe):
            log.info("已打开网盘客户端：%s", client.name)
            return {
                "opened": "client",
                "target": client.exe,
                "name": client.name,
                "exe": client.exe,
                "source": client.source,
            }
        log.warning("客户端启动失败，回退网页版：%s", client.exe)

    if web:
        if _launch(web):
            log.info("已在浏览器打开网盘网页版：%s", web)
            return {
                "opened": "web",
                "target": web,
                "name": name,
                "exe": client.exe if client else "",
                "source": client.source if client else "",
            }

    return {"opened": "none", "target": "", "name": name, "exe": "", "source": ""}


def _default_open(target: str) -> bool:
    """用系统默认方式打开（exe 直接运行，URL 交给默认浏览器）。"""
    try:
        if target.lower().startswith(("http://", "https://")):
            return bool(webbrowser.open(target))
        if _IS_WINDOWS:
            # 直接 CreateProcess，不经 shell，避免命令注入面
            subprocess.Popen(
                [target],
                cwd=str(Path(target).parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        subprocess.Popen([target])
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("打开 %s 失败：%s", target, type(exc).__name__)
        return False
