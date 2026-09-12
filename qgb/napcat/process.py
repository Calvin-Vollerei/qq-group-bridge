"""NapCat 进程托管。

职责：
  * 定位/安装 NapCat（**不随分发包捆绑**，避免再分发腾讯客户端二进制）
  * 启动 / 停止 / 健康检查
  * 从 NapCat 本地配置里读出 WebUI 令牌（写进加密凭据库，**绝不落日志**）

⚠️ 启动器文件名与 WebUI 配置路径在 NapCat 版本间有差异，因此这里都用
「候选列表 + 命中即记住」的方式，真机联调时日志会告诉你命中了哪一条。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

from ..config import NapCatConfig
from ..errors import NapCatError
from ..redact import register_secret
from ..utils import human_size

__all__ = ["NapCatManager", "NapCatStatus", "NAPCAT_RELEASES_URL", "is_admin", "resolve_qq_path"]

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ 环境探测

def is_admin() -> bool:
    """当前进程是否具备管理员权限。

    **为什么这个判断很关键**：NapCat 挂钩已安装的 QQ NT 时，需要把一个
    Hook DLL 注入到 QQ 进程里。这一步在 Windows 上需要管理员权限，
    否则注入会**静默失败** —— 表现为 NapCat 完全没反应、没有端口、没有日志，
    排查起来非常费劲。所以程序要主动检测并明确告知用户。
    """
    if not _IS_WINDOWS:
        return hasattr(os, "geteuid") and os.geteuid() == 0
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _qq_exe_from(value: str) -> str:
    """把注册表里的一个值解析成 QQ.exe 的绝对路径。"""
    raw = (value or "").strip().strip('"')
    if not raw:
        return ""
    path = Path(raw)
    candidates = [
        path / "QQ.exe",          # Install = 安装目录
        path.parent / "QQ.exe",   # UninstallString = ...\Uninstall.exe
        path,                     # 本身就是 QQ.exe
    ]
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.name.lower() == "qq.exe":
                return str(candidate.resolve())
        except OSError:
            continue
    return ""


def resolve_qq_path(explicit: str = "") -> str:
    """定位已安装的 QQ NT 入口。

    优先用显式配置；否则读注册表 —— 与 NapCat 的 ``launcher.bat`` 同逻辑，
    但**同时尝试多个注册表键**，因为不同安装方式写入的键不一样，
    只认一个键很容易在这类「安装目录不规范」的机器上失败。
    """
    if explicit:
        resolved = _qq_exe_from(explicit)
        if resolved:
            return resolved

    if not _IS_WINDOWS:
        return ""

    try:
        import winreg
    except ImportError:
        return ""

    keys = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Tencent\QQNT"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Tencent\QQNT"),
    )
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                for value_name in ("Install", "UninstallString", "DisplayIcon"):
                    try:
                        value, _ = winreg.QueryValueEx(key, value_name)
                    except OSError:
                        continue
                    found = _qq_exe_from(str(value))
                    if found:
                        return found
        except OSError:
            continue
    return ""

#: 官方发布页（用于引导手动安装；不硬编码具体资源 URL，避免版本漂移失效）
NAPCAT_RELEASES_URL = "https://github.com/NapNeko/NapCatQQ/releases"

#: 启动器候选（相对安装目录）
#: 启动器候选（相对安装目录），按「越靠前越优先」排列。
#:
#: 覆盖三种真实布局：
#:   * **OneKey 无头绿色版** —— ``NapCatInstaller.exe`` + ``bootmain/``（推荐）
#:   * 解压出来的 Shell 包 —— 根目录的 ``napcat.bat`` / ``node.exe``
#:   * 挂钩已装客户端的布局 —— ``launcher.bat``（需要 NT 架构的 QQ/TIM）
_LAUNCHER_CANDIDATES = (
    # 挂钩模式（把 NapCat.Shell.zip 解到 shell/ 下，注入已安装的 QQ NT）
    "shell/NapCatWinBootMain.exe",
    "NapCat.Shell/NapCatWinBootMain.exe",
    # OneKey 布局：NapCatWinBootMain.exe 自己会去找同级 QQ NT
    "bootmain/NapCatWinBootMain.exe",
    "bootmain/napcat.bat",
    "NapCat.Shell/bootmain/NapCatWinBootMain.exe",
    # Shell 包布局
    "napcat.bat",
    "NapCat.Shell/napcat.bat",
    "NapCat.Shell/launcher.bat",
    "launcher.bat",
    "start.bat",
    "NapCatWinBootMain.exe",
    "shell/launcher.bat",
)

#: WebUI / OneBot 配置文件候选（用于自动读取令牌与端口）。
#: 注意 OneKey 布局会把 NapCat 放进
#: ``NapCat.<build>.Shell/versions/<ver>/resources/app/napcat/config/``，
#: 因此在 :meth:`NapCatManager._config_search_roots` 里做了递归探测。
_CONFIG_CANDIDATES = (
    "config/webui.json",
    "config/onebot11.json",
    "napcat/config/webui.json",
    "NapCat.Shell/config/webui.json",
)

_IS_WINDOWS = sys.platform == "win32"


@dataclass(slots=True)
class NapCatStatus:
    installed: bool
    running: bool
    install_dir: str
    launcher: str = ""
    pid: int | None = None
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "installed": self.installed,
            "running": self.running,
            "install_dir": self.install_dir,
            "launcher": self.launcher,
            "pid": self.pid,
            "note": self.note,
        }


class NapCatManager:
    """NapCat 生命周期管理器。"""

    def __init__(self, cfg: NapCatConfig, data_dir: Path | str, *, log_dir: Path | str | None = None) -> None:
        self.cfg = cfg
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir) if log_dir else self.data_dir / "logs"
        self._proc: subprocess.Popen | None = None
        self._launcher: str = ""

    # -------------------------------------------------- 路径

    @property
    def install_dir(self) -> Path:
        """QQ 组件的安装目录。

        解析顺序：

        1. 配置里显式指定的 ``install_dir``
        2. **自动探测「自带运行时」布局**（``napcat-embedded/``，其次
           ``napcat/``）—— 判断依据是同时存在 ``node.exe`` 与 ``index.js``。
        3. 默认 ``<data>/napcat``

        第 2 步为什么必要：自带运行时是**更优**的形态（自包含、免管理员、
        不受用户 QQ 版本影响）。早先靠配置项指向它，结果程序保存配置时
        把该字段覆盖回空值，于是又退回了挂钩模式 —— 靠配置记住这种事不可靠，
        必须让程序自己认出来。
        """
        if self.cfg.install_dir:
            return Path(self.cfg.install_dir).expanduser()

        fallback = self.data_dir / "napcat"
        for name in ("napcat-embedded", "napcat"):
            candidate = self.data_dir / name
            try:
                if (candidate / "node.exe").is_file() and (candidate / "index.js").is_file():
                    return candidate
            except OSError:
                continue
        return fallback

    def launcher_path(self) -> Path | None:
        """返回命中的启动器路径（会缓存上次命中的结果）。"""
        base = self.install_dir
        order = ([self._launcher] if self._launcher else []) + list(_LAUNCHER_CANDIDATES)
        seen: set[str] = set()
        for rel in order:
            if not rel or rel in seen:
                continue
            seen.add(rel)
            candidate = base / rel
            if candidate.is_file():
                self._launcher = rel
                return candidate
        return None

    def is_installed(self) -> bool:
        return self.launcher_path() is not None

    # -------------------------------------------------- 状态

    def is_running(self) -> bool:
        """NapCat 是否**可用**。

        判据是「本程序启动的进程还活着」**或**「它的接口端口有响应」。

        为什么不用「按进程名找 node.exe」：机器上可能有别的 Node 程序
        （甚至本工具的运行环境自带 node）。按名字匹配会把它们误认成 NapCat，
        进而让状态显示错误、甚至让 :meth:`stop` 去杀掉无关进程 ——
        这是必须避免的破坏性误判。端口探测才是「能不能用」的真实判据。
        """
        if self._proc is not None and self._proc.poll() is None:
            return True
        return self._port_open(self.cfg.api_base) or self._port_open(self.cfg.webui_base)

    @staticmethod
    def _port_open(url: str, timeout: float = 1.0) -> bool:
        """快速探测某个 URL 的主机:端口是否有服务在监听。"""
        if not url:
            return False
        try:
            import socket
            from urllib.parse import urlparse

            parsed = urlparse(url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with socket.create_connection((host, int(port)), timeout=timeout):
                return True
        except (OSError, ValueError):
            return False

    def status(self) -> NapCatStatus:
        launcher = self.launcher_path()
        running = self.is_running()
        pid = self._proc.pid if (self._proc and self._proc.poll() is None) else None
        note = ""
        if not launcher:
            note = "未安装：请在「QQ 登录」页点击「安装 QQ 组件」"
        elif not running:
            note = "已安装但未运行"
        elif pid is None:
            note = "已在运行（由其他方式启动）"

        # 缺依赖是「装了但起不来」的最常见原因，且报错极具误导性
        missing = self.embedded_runtime_problems()
        if missing:
            note = (
                "自带运行时缺少必要文件，无法启动："
                + "、".join(missing)
                + "。请从 QQ NT 客户端目录复制这些文件过来"
                  "（通常位于 ...\\versions\\<版本>\\resources\\app\\）。"
            )

        return NapCatStatus(
            installed=launcher is not None,
            running=running,
            install_dir=str(self.install_dir),
            launcher=str(launcher) if launcher else "",
            pid=pid,
            note=note,
        )

    def last_log_tail(self, lines: int = 25) -> str:
        """读取最近一次启动的输出（排查启动失败用，已脱敏）。"""
        chunks: list[str] = []
        for name in ("napcat.out.log", "napcat.err.log"):
            path = self.log_dir / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                continue
            if text:
                chunks.append(f"--- {name} ---\n" + "\n".join(text.splitlines()[-lines:]))
        from ..redact import redact_text

        return redact_text("\n".join(chunks))

    # -------------------------------------------------- 启停

    def hook_layout(self) -> tuple[Path, Path, Path] | None:
        """检测「挂钩已安装 QQ NT」的布局。

        只认**专门的挂钩目录** ``shell/`` 与 ``NapCat.Shell/``。

        ⚠️ 刻意**不把 ``napcat/`` 算进来**：``NapCat.Shell.Windows.Node.zip``
        这类自带运行时的包里，``napcat/`` 是负载目录，里面同样有
        ``NapCatWinBootMain.exe`` + ``NapCatWinBootHook.dll``。若把它当成挂钩
        布局，程序就会去挂钩用户已安装的 QQ —— 而那正是我们要避开的
        （需要管理员权限，且受 QQ 版本限制）。这种包应该走 node 直调。

        返回 ``(backend_exe, hook_dll, napcat_dir)``；不匹配则返回 ``None``。
        """
        for name in ("shell", "NapCat.Shell"):
            base = self.install_dir / name
            backend = base / "NapCatWinBootMain.exe"
            hook = base / "NapCatWinBootHook.dll"
            if backend.is_file() and hook.is_file():
                return backend, hook, base
        return None

    def write_loader(self, napcat_dir: Path) -> Path:
        """生成 ``loadNapCat.js`` —— 它负责 import ``napcat.mjs``。

        NapCat 的 ``launcher.bat`` 本来会生成这个文件，但它是用 cmd 的
        ``echo`` 重定向写的，**中文路径经 cmd 代码页转换容易损坏**。
        这里用 Python 直接写，并把路径统一成正斜杠（file:// URL 要求）。
        """
        main = (napcat_dir / "napcat.mjs").resolve().as_posix()
        loader = napcat_dir / "loadNapCat.js"
        loader.write_text(
            f'(async () => {{await import("file:///{main}")}})()',
            encoding="utf-8",
        )
        return loader

    def start_env(self, napcat_dir: Path) -> dict[str, str]:
        """挂钩模式需要的环境变量（写清每个变量的用途，便于排障）。"""
        env = dict(os.environ)
        env["NAPCAT_PATCH_PACKAGE"] = str((napcat_dir / "qqnt.json").resolve())
        env["NAPCAT_LOAD_PATH"] = str((napcat_dir / "loadNapCat.js").resolve())
        env["NAPCAT_INJECT_PATH"] = str((napcat_dir / "NapCatWinBootHook.dll").resolve())
        env["NAPCAT_LAUNCHER_PATH"] = str((napcat_dir / "NapCatWinBootMain.exe").resolve())
        env["NAPCAT_MAIN_PATH"] = (napcat_dir / "napcat.mjs").resolve().as_posix()
        if self.cfg.single_process:
            # 挂钩模式下同一个开关同样有用：避免 worker 走命名管道
            env["NAPCAT_DISABLE_MULTIPROCESSING"] = "1"
            env["NAPCAT_DISABLE_MULTI_PROCESS"] = "1"
        return env

    def embedded_runtime_problems(self) -> list[str]:
        """检查「自带运行时」是否缺关键文件。

        **为什么要专门检查这个**：``NapCat.Shell.Windows.Node.zip`` 里的
        ``wrapper.node`` 依赖 ``crypto.dll`` 与 ``ssl.dll``，而这两个文件
        **不在包里** —— 它们来自 QQ NT 客户端本身。缺了会报
        「The specified module could not be found」，**症状极具误导性**
        （看起来像是文件不存在，其实是间接依赖缺失），排查代价很高。
        所以这里提前诊断并给出可操作的结论。
        """
        base = self.install_dir
        if not (base / "node.exe").is_file() or not (base / "index.js").is_file():
            return []          # 不是自带运行时布局，不适用

        required = {
            "wrapper.node": "QQ NT 核心模块",
            "QQNT.dll": "QQ NT 运行库",
            "crypto.dll": "OpenSSL 加密库（来自 QQ NT 客户端）",
            "ssl.dll": "OpenSSL 传输层库（来自 QQ NT 客户端）",
        }
        missing = [
            f"{name}（{desc}）"
            for name, desc in required.items()
            if not (base / name).is_file()
        ]
        return missing

    def launcher_command(self) -> tuple[list[str], str]:
        """确定启动命令，返回 ``(命令行, 工作目录)``。

        按布局优先级：

        1. **node 直调** —— ``node.exe index.js``：这类包**自带 QQ NT 运行时**
           （``NapCat.Shell.Windows.Node.zip``），完全自包含，**不需要管理员、
           不挂钩用户的 QQ、也不受用户 QQ 版本影响**。优先级最高。
        2. **挂钩模式** —— ``shell/NapCatWinBootMain.exe <QQ.exe> <Hook.dll>``，
           注入已安装的 QQ NT。**需要管理员权限**，且受 QQ 版本限制。
        3. **bootmain 无参** —— 启动自带 QQ 的绿色包。

        ⚠️ 所有路径必须 **绝对化**：node 会把相对路径的脚本参数按 cwd 再解析，
        于是 ``node.exe <base>/index.js``（相对）会拼成 ``<base>/<base>/index.js``
        并报 MODULE_NOT_FOUND。
        """
        base = self.install_dir.resolve()

        # 1) 自带运行时的自包含模式
        node = base / "node.exe"
        entry = base / "index.js"
        if node.is_file() and entry.is_file():
            return [str(node), str(entry)], str(base)

        # 2) 挂钩已安装的 QQ
        hook = self.hook_layout()
        if hook is not None:
            backend, hook_dll, napcat_dir = hook
            qq_path = resolve_qq_path(self.cfg.qq_path)
            if qq_path:
                self.write_loader(napcat_dir)
                return (
                    [str(backend.resolve()), qq_path, str(hook_dll.resolve())],
                    str(napcat_dir.resolve()),
                )
            log.warning("检测到挂钩布局但没找到 QQ.exe，回退到其他启动方式")

        # 3) bootmain（自带 QQ 的绿色包）
        bootmain = base / "bootmain" / "NapCatWinBootMain.exe"
        if bootmain.is_file():
            return [str(bootmain.resolve())], str(bootmain.parent.resolve())

        launcher = self.launcher_path()
        if launcher is None:
            return [], str(base)
        launcher_abs = launcher.resolve()
        return [str(launcher_abs)], str(launcher_abs.parent)

    def start(self, *, wait_ready: float = 0.0, client=None) -> NapCatStatus:
        """启动 NapCat。已运行则直接返回当前状态。"""
        if self.is_running():
            return self.status()

        if not self.is_installed():
            raise NapCatError(
                "未找到 NapCat，请先安装。",
                hint=(
                    "在「QQ 登录」页点击「安装 QQ 组件」，或手动从 "
                    f"{NAPCAT_RELEASES_URL} 下载后解压到安装目录。"
                ),
            )

        cmd, cwd = self.launcher_command()
        if not cmd:
            raise NapCatError("无法确定 NapCat 启动方式", hint="请确认安装目录完整。")

        # 挂钩模式必须管理员，否则注入会**静默失败**（没有任何报错，最难排查）
        hook = self.hook_layout()
        if hook is not None and cmd and cmd[0].lower().endswith("napcatwinbootmain.exe"):
            if not is_admin():
                raise NapCatError(
                    "以管理员身份运行才能登录 QQ",
                    hint=(
                        "NapCat 需要把 Hook 注入到 QQ 进程，Windows 下这一步要求管理员权限，"
                        "否则会静默失败（界面看起来毫无反应）。\n\n"
                        "请关闭本程序，然后右键「启动搬运工.bat」→「以管理员身份运行」。"
                    ),
                )

        # 日志按次清空：追加写入会把多次启动的日志混在一起，排障困难
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.log_dir / "napcat.out.log"
        stderr_path = self.log_dir / "napcat.err.log"
        for path in (stdout_path, stderr_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

        creationflags = 0
        if _IS_WINDOWS:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        uses_shell = cmd[0].lower().endswith((".bat", ".cmd"))

        # 环境变量：挂钩模式需要一整套 NAPCAT_* 变量才能找到负载
        env = dict(os.environ)
        if hook is not None:
            env = self.start_env(hook[2])
        elif self.cfg.single_process:
            # 单进程模式：规避 worker 走命名管道 IPC（受限环境下 spawn EPERM）
            # 以及 Electron 专有的 --no-sandbox 被 node 拒绝的问题
            env["NAPCAT_DISABLE_MULTIPROCESSING"] = "1"
            env["NAPCAT_DISABLE_MULTI_PROCESS"] = "1"

        try:
            out_fh = stdout_path.open("wb")
            err_fh = stderr_path.open("wb")
            self._proc = subprocess.Popen(
                cmd if not uses_shell else cmd[0],
                cwd=cwd,
                stdout=out_fh,
                stderr=err_fh,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=creationflags,
                shell=uses_shell,
            )
        except OSError as exc:
            raise NapCatError(
                f"启动 NapCat 失败：{exc}",
                hint="请确认安装目录完整，且杀毒软件没有拦截。",
            ) from exc

        log.info(
            "已启动 QQ 组件（pid=%s，方式=%s%s）",
            self._proc.pid,
            "挂钩已装 QQ" if hook is not None else (
                "node 直调" if not uses_shell else "bat"
            ),
            "，单进程模式" if self.cfg.single_process else "",
        )

        if wait_ready and client is not None:
            ok = client.wait_until_ready(timeout=wait_ready, interval=2.0)
            if not ok:
                log.warning("QQ 组件在 %.0f 秒内未就绪，可能仍在启动", wait_ready)

        return self.status()

    def stop(self, *, timeout: float = 15.0) -> None:
        """停止 NapCat（连同它自己拉起的子进程）。

        **只结束本程序启动的那棵进程树。**
        绝不按进程名去杀 —— 机器上可能存在其它 Node 程序（甚至本工具的
        运行环境自带 node），按名字杀会造成无关进程被终止这种破坏性后果。
        若是用户自己启动的 NapCat，提示其手动关闭即可。
        """
        if self._proc is None or self._proc.poll() is not None:
            if self._port_open(self.cfg.api_base) or self._port_open(self.cfg.webui_base):
                log.info("NapCat 在运行但不是由本程序启动，跳过自动停止")
            else:
                log.info("QQ 组件未在运行")
            self._proc = None
            return

        pid = self._proc.pid
        try:
            if _IS_WINDOWS:
                # /T 会一并结束子进程（QQ NT 内核是独立的子进程）
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                os.kill(pid, 15)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("停止进程 %s 时出现问题：%s", pid, exc.__class__.__name__)

        self._proc = None
        log.info("已停止 QQ 组件")

    def restart(self, **kw) -> NapCatStatus:
        self.stop()
        time.sleep(1.5)
        return self.start(**kw)

    # -------------------------------------------------- 安装

    def install_from_zip(self, zip_path: Path | str, *, overwrite: bool = False) -> Path:
        """从本地压缩包安装（离线/内网场景）。"""
        zip_path = Path(zip_path)
        if not zip_path.is_file():
            raise NapCatError(f"安装包不存在：{zip_path.name}")

        dest = self.install_dir
        if dest.exists() and any(dest.iterdir()) and not overwrite:
            raise NapCatError(
                f"安装目录非空：{dest}",
                hint="如需覆盖安装，请先清空该目录，或勾选「覆盖安装」。",
            )
        dest.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(zip_path) as zf:
                _safe_extract(zf, dest)
        except (zipfile.BadZipFile, OSError) as exc:
            raise NapCatError(f"解压失败：{exc}") from exc

        # 压缩包可能多套了一层目录，尝试向下探测
        if self.launcher_path() is None:
            for child in dest.iterdir():
                if child.is_dir() and (child / "launcher.bat").exists():
                    log.info("安装包内含一层目录，启动器位于 %s", child.name)
                    self._launcher = f"{child.name}/launcher.bat"
                    break

        if self.launcher_path() is None:
            raise NapCatError(
                "解压完成但未找到启动器，安装包结构可能不符。",
                hint=f"请从官方发布页下载：{NAPCAT_RELEASES_URL}",
            )

        log.info("QQ 组件安装完成：%s", dest)
        return dest

    # -------------------------------------------------- 配置预置

    def ensure_configs(
        self,
        *,
        webui_token: str | None = None,
        webui_port: int = 6099,
        onebot_port: int = 3000,
        force_webui_token: bool = False,
    ) -> dict[str, object]:
        """预置 NapCat 配置，让「启动即可扫码」成立。

        为什么要预置：NapCat **默认不开启** OneBot 的 HTTP 服务，新用户必须在
        NapCat 网页端手点「新建 → HTTP 服务器 → 保存并启用」。对「打开软件就能用」
        而言这是多余摩擦，而且很容易把端口配错。我们在启动前把配置写好，
        用户只需要扫码。

        写两个文件（结构均已按 NapCat 源码/文档核对）::

            config/webui.json       {"host","port","token","loginRate"}
            config/onebot11.json    {"network":{"httpServers":[{...}]}}

        已有配置**不会被覆盖**（除非显式传入新 token），避免破坏用户既有设置。
        """
        config_dir = self.config_root() / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        created: list[str] = []

        # ---------------- config/webui.json
        webui_path = config_dir / "webui.json"
        existing: dict = {}
        if webui_path.is_file():
            try:
                loaded = json.loads(webui_path.read_text(encoding="utf-8", errors="ignore"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, json.JSONDecodeError):
                existing = {}

        token = webui_token or str(existing.get("token") or "") or os.urandom(24).hex()
        need_write = (not webui_path.is_file()) or force_webui_token or bool(webui_token)

        if need_write:
            payload = {
                "host": "127.0.0.1",  # 只监听本机，不对外暴露
                "port": int(webui_port),
                "token": token,
                # NapCat 的默认值是 3 次/分钟。这个值对本工具偏低：
                # 配置期一旦出现几次令牌不匹配（例如切换过运行方式），
                # 就会撞上 "login rate limit"，把用户挡在门外一分钟。
                # WebUI 只监听 127.0.0.1，放宽到 10 仍然足够安全。
                "loginRate": 10,
            }
            webui_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            created.append(str(webui_path))
            register_secret(token)

        # ---------------- config/onebot11.json
        onebot_path = config_dir / "onebot11.json"
        if not onebot_path.is_file():
            payload = {
                "network": {
                    "httpServers": [
                        {
                            "name": "qgb-http",
                            "enable": True,
                            "port": int(onebot_port),
                            "host": "127.0.0.1",
                            "enableCors": True,
                            "enableWebsocket": True,
                            "messagePostFormat": "array",
                            "token": "",
                            "debug": False,
                        }
                    ],
                    "httpClients": [],
                    "websocketServers": [],
                    "websocketClients": [],
                },
                "musicSignUrl": "",
                "enableLocalFile2Url": False,
                "parseMultMsg": False,
            }
            onebot_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            created.append(str(onebot_path))

        if created:
            log.info("已预置 NapCat 配置 %d 个文件", len(created))

        return {
            "config_dir": str(config_dir),
            "webui_path": str(webui_path),
            "onebot_path": str(onebot_path),
            "webui_token": token,
            "webui_port": int(webui_port),
            "onebot_port": int(onebot_port),
            "created": created,
        }

    def _napcat_app_dirs(self) -> list[Path]:
        """找出所有可能的 NapCat 应用目录（含 ``napcat.mjs`` 的目录）。

        OneKey 无头绿色版会把 NapCat 放进很深的嵌套路径::

            NapCat.<build>.Shell/versions/<version>/resources/app/napcat/napcat.mjs

        这里用 glob 探测，避免把版本号写死在代码里。
        """
        base = self.install_dir
        found: list[Path] = []

        for rel in ("", "napcat"):
            candidate = base / rel if rel else base
            if (candidate / "napcat.mjs").is_file():
                found.append(candidate)

        for pattern in (
            "NapCat.*.Shell/versions/*/resources/app/napcat",
            "*.Shell/versions/*/resources/app/napcat",
            "versions/*/resources/app/napcat",
            "*/versions/*/resources/app/napcat",
            # 挂钩模式：把 NapCat.Shell.zip 解到安装目录下的 shell/ 里
            "shell",
            "*/shell",
            "NapCat.Shell",
            "*/NapCat.Shell",
        ):
            try:
                for path in base.glob(pattern):
                    if path.is_dir() and (path / "napcat.mjs").is_file():
                        found.append(path)
            except OSError:
                continue

        unique: list[Path] = []
        for path in found:
            if path not in unique:
                unique.append(path)
        return unique

    def _config_roots(self) -> list[Path]:
        """配置文件可能出现的目录（按优先级）。"""
        roots = [
            self.install_dir,
            self.install_dir / "bootmain",
            self.install_dir / "napcat",
            *self._napcat_app_dirs(),
        ]
        unique: list[Path] = []
        for root in roots:
            try:
                if root.is_dir() and root not in unique:
                    unique.append(root)
            except OSError:
                continue
        return unique

    def config_root(self) -> Path:
        """应该把配置写到哪个目录。

        优先写进真正的 NapCat 应用目录（OneKey 布局下它在嵌套路径里），
        否则退回安装根目录。
        """
        app_dirs = self._napcat_app_dirs()
        if app_dirs:
            return app_dirs[0]
        return self.install_dir

    def read_webui_token_from_config(self) -> str:
        """从任意候选位置读取 ``webui.json`` 里的令牌（不落日志）。"""
        for root in self._config_roots():
            path = root / "config" / "webui.json"
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                token = str(data.get("token") or "").strip()
                if token:
                    register_secret(token)
                    log.info("已从 %s 读取 WebUI 令牌", path.parent.parent.name)
                    return token
        return ""

    def install_instructions(self) -> str:
        """给 GUI 显示的安装引导（含官方地址，不内置任何二进制）。"""
        return (
            "首次使用需要安装 QQ 组件（NapCat）。推荐用官方的一键无头绿色版：\n\n"
            f"1. 打开发布页：{NAPCAT_RELEASES_URL}\n"
            "2. 下载 NapCat.Shell.Windows.OneKey.zip（约 1 MB）\n"
            f"3. 解压到：{self.install_dir}\n"
            "4. 双击其中的 NapCatInstaller.exe，等它自动下载 QQ 运行时\n"
            "5. 回到本页点「刷新状态」，再点「启动组件」\n\n"
            "为什么会失败？如果第 4 步报「下载 QQ 失败」，那是官方安装器里写死的\n"
            "QQ 下载地址已经过期（腾讯会定期换版本）。解决办法：\n"
            "  · 到 im.qq.com 用浏览器下载新版 QQ 安装包，然后用\n"
            "    scripts/assemble_napcat.py 离线组装；或\n"
            "  · 直接安装官方 QQ（NT 版），本程序会用 launcher.bat 挂钩它。\n\n"
            "说明：出于安全与授权考虑，本程序不捆绑、不分发腾讯的客户端二进制。"
        )

    # -------------------------------------------------- 配置读取

    def discover_webui_token(self) -> str:
        """从 NapCat 本地配置里读出 WebUI 令牌。

        这是本机自己的组件配置，读取它是为了省去让部署方手抄令牌。
        读到的值会被登记进脱敏表，**绝不会出现在日志里**。
        """
        token = self.read_webui_token_from_config()
        if token:
            return token

        for rel in _CONFIG_CANDIDATES:
            path = self.install_dir / rel
            if path.is_file():
                token = _extract_token(path)
                if token:
                    register_secret(token)
                    log.info("已从 %s 读取到 WebUI 令牌（已加密保存）", rel)
                    return token
        return ""


# ------------------------------------------------------------------ 工具

def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """阻止 zip slip（``../`` 路径穿越）。"""
    dest_resolved = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if dest_resolved != target and dest_resolved not in target.parents:
            raise NapCatError(f"安装包包含非法路径，已中止：{member.filename}")
    zf.extractall(dest)


def _extract_token(path: Path) -> str:
    """从 JSON 配置里挖出可能的令牌字段（键名做兼容）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""

    for key in ("token", "webuiToken", "webui_token", "accessToken"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    nested = data.get("webui")
    if isinstance(nested, dict):
        for key in ("token", "webuiToken"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def find_disk_hint(path: Path | str) -> str:
    """给 GUI 的磁盘提示（安装目录所在盘剩余空间）。"""
    from ..utils import free_disk_bytes

    return human_size(free_disk_bytes(path))


def terminate_self_children() -> None:  # pragma: no cover - 退出清理
    """退出前尽力清理（GUI 关闭时调用）。"""
    if not _IS_WINDOWS:
        return
    try:
        subprocess.run(
            ["taskkill", "/IM", "NapCatWinBootMain.exe", "/T", "/F"],
            capture_output=True, timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        pass


def which_python() -> str:
    return shutil.which("python") or sys.executable
