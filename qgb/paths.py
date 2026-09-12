"""数据目录解析：**保证任何环境下都能拿到一个可写目录**。

为什么需要这一层：
  * 部署方的电脑可能是**受限账户**、被组策略重定向了 ``%LOCALAPPDATA%``、
    或者装在需要管理员权限的目录下 —— 这时直接写默认路径会 ``PermissionError``
  * 「绿色免安装包」的诉求恰恰是**数据跟着程序走**，而不是散落在系统盘

解析顺序（命中第一个可写的即采用）：

  1. 环境变量 ``QGB_DATA_DIR``（显式指定，部署脚本/多实例都用得上）
  2. **便携模式**：程序目录下存在 ``portable.marker`` → 用 ``程序目录/data``
  3. ``%LOCALAPPDATA%\\QQGroupBridge``（标准做法）
  4. 系统临时目录 ``%TEMP%\\QQGroupBridge``
  5. 程序目录下的 ``.data``（最后兜底）

每一步都会**实际写一个探针文件**来判断可写性，而不是只看 ``os.access``
（后者在 Windows 上对 ACL 的判断并不可靠）。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from .version import DATA_DIR_NAME

__all__ = [
    "resolve_data_dir",
    "data_dir_source",
    "is_portable",
    "app_base_dir",
    "reset_cache",
    "candidate_dirs",
]

_resolved: Path | None = None
_source: str = ""


def app_base_dir() -> Path:
    """程序所在目录（打包后是 exe 所在目录，开发时是项目根）。"""
    if getattr(sys, "frozen", False):  # PyInstaller
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def is_portable() -> bool:
    """是否处于便携模式（程序目录下有 portable.marker）。"""
    try:
        return (app_base_dir() / "portable.marker").exists()
    except OSError:
        return False


def _probe_writable(path: Path) -> bool:
    """真正写一个探针文件来确认可写（比 os.access 可靠）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".qgb-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except (OSError, PermissionError):
        return False


def candidate_dirs() -> list[tuple[str, Path]]:
    """返回 ``[(来源说明, 路径), ...]``，按优先级排列。"""
    out: list[tuple[str, Path]] = []

    env = os.environ.get("QGB_DATA_DIR")
    if env:
        out.append(("环境变量 QGB_DATA_DIR", Path(env).expanduser()))

    base = app_base_dir()
    if is_portable():
        out.append(("便携模式（程序目录/data）", base / "data"))

    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        out.append(("%LOCALAPPDATA%", Path(local) / DATA_DIR_NAME))
    else:
        out.append(("用户主目录", Path.home() / f".{DATA_DIR_NAME.lower()}"))

    out.append(("系统临时目录", Path(tempfile.gettempdir()) / DATA_DIR_NAME))
    out.append(("程序目录（兜底）", base / ".data"))

    return out


def resolve_data_dir(*, force: bool = False) -> Path:
    """解析出实际使用的数据目录（结果会缓存）。"""
    global _resolved, _source
    if _resolved is not None and not force:
        return _resolved

    for label, path in candidate_dirs():
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if _probe_writable(resolved):
            _resolved, _source = resolved, label
            return resolved

    # 理论上到不了这里（临时目录通常可写）；真到了就用临时目录硬上
    fallback = Path(tempfile.gettempdir()) / DATA_DIR_NAME
    _resolved, _source = fallback, "系统临时目录（强制）"
    return fallback


def data_dir_source() -> str:
    """返回数据目录的来源说明（用于界面/自检显示）。"""
    resolve_data_dir()
    return _source


def reset_cache() -> None:
    """仅供测试：清空缓存，让下次重新解析。"""
    global _resolved, _source
    _resolved = None
    _source = ""


def ensure_subdir(name: str) -> Path:
    """在数据目录下取（并创建）一个子目录。"""
    path = resolve_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path
