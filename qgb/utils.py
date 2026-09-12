"""通用小工具。"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import time
from pathlib import Path

__all__ = [
    "human_size",
    "human_duration",
    "safe_filename",
    "unique_path",
    "sha256_file",
    "free_disk_bytes",
    "now_str",
    "clamp",
]

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def human_size(num: float | int | None) -> str:
    """1536 → ``1.5 KB``"""
    if num is None:
        return "-"
    n = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def safe_filename(name: str, *, max_len: int = 180) -> str:
    """把群文件名清洗成跨平台安全的本地文件名。

    群文件名可能包含 ``/``、控制字符、超长 emoji 串，甚至保留设备名，
    直接落盘会失败或造成路径穿越。
    """
    name = (name or "").strip()
    name = name.replace("\\", "_").replace("/", "_")
    name = _ILLEGAL.sub("_", name)
    name = name.strip(" .")

    if not name:
        name = "unnamed"

    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    if stem.upper() in _WIN_RESERVED:
        stem = f"_{stem}"

    # 按字节安全地截断（保留扩展名）
    budget = max(16, max_len - (len(ext) + 1 if ext else 0))
    if len(stem) > budget:
        stem = stem[:budget]

    return f"{stem}.{ext}" if ext else stem


def unique_path(path: Path) -> Path:
    """若目标已存在，追加 ``_1`` / ``_2`` …"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(1, 10000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"无法为 {path.name} 生成唯一文件名")


def sha256_file(path: Path | str, *, chunk: int = 1024 * 1024, progress=None) -> str:
    h = hashlib.sha256()
    total = 0
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
            total += len(block)
            if progress and size:
                progress(total, size)
    return h.hexdigest()


def free_disk_bytes(path: Path | str) -> int:
    target = Path(path)
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return shutil.disk_usage(str(target)).free
    except OSError:
        return 1 << 62  # 取不到就不要误判为磁盘不足


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    return time.strftime(fmt)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
