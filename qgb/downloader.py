"""断点续传下载器。

针对 QQ 群文件直链的特点做了三件专门处理：
  1. **301/302 跟随** —— NapCat 拿到的直链常常是跳转链
  2. **Range 断点续传** —— 大文件中断后不重下；同时正确处理
     服务器「忽略 Range 直接返回 200」的情况（此时必须截断重写）
  3. **签名过期识别** —— 直链有时效，返回 403/410 时立刻放弃并让上层重新取链
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from .errors import DownloadError
from .utils import free_disk_bytes, human_size, sha256_file

__all__ = ["Downloader", "DownloadResult", "DownloadProgress"]

log = logging.getLogger(__name__)

ProgressCb = Callable[[int, int], None]


@dataclass(slots=True)
class DownloadProgress:
    """一次下载的进度快照（供 GUI 显示）。"""

    name: str
    downloaded: int
    total: int
    speed_bps: float
    eta_sec: float

    @property
    def percent(self) -> float:
        return (self.downloaded / self.total * 100) if self.total else 0.0


@dataclass(slots=True)
class DownloadResult:
    path: Path
    size: int
    sha256: str
    elapsed: float
    resumed_from: int = 0

    @property
    def speed_bps(self) -> float:
        return self.size / self.elapsed if self.elapsed > 0 else 0.0


class Downloader:
    """带重试与断点续传的流式下载器。"""

    def __init__(
        self,
        *,
        chunk_size: int = 256 * 1024,
        timeout: tuple[float, float] = (15.0, 60.0),
        max_retries: int = 3,
        backoff_base: float = 5.0,
        user_agent: str = "qgb/0.1 (+group-file-bridge)",
    ) -> None:
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self.backoff_base = backoff_base
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    # -------------------------------------------------- 内部

    def _measure_free(self, dest_dir: Path, need: int) -> None:
        free = free_disk_bytes(dest_dir)
        # 预留 64MB 余量
        if need and free < need + 64 * 1024 * 1024:
            raise DownloadError(
                f"磁盘空间不足：需要约 {human_size(need)}，剩余 {human_size(free)}",
                hint="请清理磁盘或把临时目录改到大容量分区。",
            )

    @staticmethod
    def _is_signature_error(status: int) -> bool:
        # 403/410/404 往往意味着直链已过期（QQ 直链有时效）
        return status in (401, 403, 404, 410)

    # -------------------------------------------------- 主流程

    def download(
        self,
        url: str,
        dest: Path | str,
        *,
        expected_size: int | None = None,
        headers: dict[str, str] | None = None,
        allow_resume: bool = True,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        compute_hash: bool = True,
    ) -> DownloadResult:
        """下载 ``url`` 到 ``dest``，返回结果。失败抛 :class:`DownloadError`。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                result = self._attempt(
                    url,
                    dest,
                    part,
                    expected_size=expected_size,
                    headers=headers,
                    allow_resume=allow_resume,
                    on_progress=on_progress,
                    compute_hash=compute_hash,
                )
                return result
            except DownloadError as exc:
                last_error = exc
                # 直链过期：重试同一个 URL 没有意义
                if "直链已过期" in str(exc):
                    raise
                if attempt < self.max_retries:
                    wait = self.backoff_base * attempt
                    log.warning(
                        "下载失败（第 %d/%d 次）：%s；%.0f 秒后重试",
                        attempt, self.max_retries, exc, wait,
                    )
                    time.sleep(wait)
            except requests.RequestException as exc:
                last_error = DownloadError(f"网络异常：{exc}")
                if attempt < self.max_retries:
                    time.sleep(self.backoff_base * attempt)

        raise DownloadError(
            f"重试 {self.max_retries} 次后仍失败：{last_error}",
            hint="请检查网络，或确认群文件是否已被上传者删除。",
        )

    def _attempt(
        self,
        url: str,
        dest: Path,
        part: Path,
        *,
        expected_size: int | None,
        headers: dict[str, str] | None,
        allow_resume: bool,
        on_progress: Callable[[DownloadProgress], None] | None,
        compute_hash: bool,
    ) -> DownloadResult:
        resumed_from = 0
        req_headers: dict[str, str] = dict(headers or {})

        if allow_resume and part.exists():
            size = part.stat().st_size
            if expected_size and size == expected_size:
                # 上次其实已经下完了，只是没改名
                os.replace(part, dest)
                return self._finalize(dest, 0, compute_hash)
            if size > 0 and (expected_size is None or size < expected_size):
                req_headers["Range"] = f"bytes={size}-"
                resumed_from = size

        self._measure_free(dest.parent, expected_size or 0)

        started = time.monotonic()
        with self.session.get(
            url,
            headers=req_headers,
            stream=True,
            timeout=self.timeout,
            allow_redirects=True,
        ) as resp:
            if self._is_signature_error(resp.status_code):
                raise DownloadError("下载直链已过期，需要重新获取", hint="将自动重新取链。")

            if resp.status_code == 416:
                # Range 越界：本地文件可能已完整
                if part.exists() and expected_size and part.stat().st_size == expected_size:
                    os.replace(part, dest)
                    return self._finalize(dest, 0, compute_hash)
                part.unlink(missing_ok=True)
                raise DownloadError("服务器拒绝断点续传（416）")

            if resp.status_code not in (200, 206):
                raise DownloadError(f"服务器返回异常状态码 {resp.status_code}")

            # 服务器忽略了 Range，返回整份内容 → 必须从头写
            mode = "ab"
            if resp.status_code == 200 and resumed_from:
                log.info("服务器不支持断点续传，从头开始下载")
                resumed_from = 0
                mode = "wb"
            elif resp.status_code == 206:
                mode = "ab"
            else:
                mode = "wb"

            declared = resp.headers.get("Content-Length")
            remaining = int(declared) if declared and declared.isdigit() else None
            total = (resumed_from + remaining) if remaining else (expected_size or 0)

            written = resumed_from
            last_emit = 0.0

            with part.open(mode) as fh:
                for block in resp.iter_content(chunk_size=self.chunk_size):
                    if not block:
                        continue
                    fh.write(block)
                    written += len(block)

                    if on_progress:
                        now = time.monotonic()
                        if now - last_emit >= 0.4:
                            last_emit = now
                            elapsed = max(1e-6, now - started)
                            speed = (written - resumed_from) / elapsed
                            eta = ((total - written) / speed) if (speed > 0 and total) else 0.0
                            on_progress(
                                DownloadProgress(
                                    name=dest.name,
                                    downloaded=written,
                                    total=total or written,
                                    speed_bps=speed,
                                    eta_sec=eta,
                                )
                            )

        actual = part.stat().st_size
        if expected_size and actual != expected_size:
            # 不完整：保留 .part 以便下次续传
            raise DownloadError(
                f"文件大小不符：期望 {human_size(expected_size)}，实际 {human_size(actual)}"
            )

        os.replace(part, dest)
        return self._finalize(dest, resumed_from, compute_hash, elapsed=time.monotonic() - started)

    def _finalize(
        self,
        dest: Path,
        resumed_from: int,
        compute_hash: bool,
        *,
        elapsed: float = 0.0,
    ) -> DownloadResult:
        size = dest.stat().st_size
        digest = sha256_file(dest) if compute_hash else ""
        return DownloadResult(
            path=dest, size=size, sha256=digest, elapsed=elapsed, resumed_from=resumed_from
        )
