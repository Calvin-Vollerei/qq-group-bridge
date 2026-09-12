"""开发/测试用假实现。

目标：**在没有任何真实账号的前提下，验证整条流水线**。

  * :class:`RangeFileServer` —— 模拟 QQ 群文件直链（支持/不支持 Range 两种模式）
  * :func:`make_secrets` —— 造一个用假后端加密的凭据库
  * :func:`make_config` —— 造一份指向本地目录的最小配置
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..config import AppConfig, FilterConfig, MonitorConfig, NapCatConfig, UploadConfig
from ..secrets import InsecureDevBackend, SecretStore

__all__ = [
    "RangeFileServer",
    "ServedFile",
    "make_secrets",
    "make_config",
    "free_port",
]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class ServedFile:
    """一条「群文件直链」的登记信息。"""

    filename: str
    content: bytes
    path: Path
    url: str = ""

    @property
    def size(self) -> int:
        return len(self.content)


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """支持 Range 的静态文件服务（用于断点续传测试）。"""

    server_version = "FakeQQDirectLink/1.0"

    # 由 server 实例注入
    support_range: bool = True
    choke_after: int = 0  # >0 时，发送该字节数后主动断开（模拟中途断流）

    def log_message(self, *args) -> None:  # 静音
        return

    def _lookup(self) -> Path | None:
        """按请求路径取文件。

        ⚠️ 必须 unquote：客户端会把中文文件名百分号编码，
        否则命不中 file_map，会误报 404（进而被上层误判为「直链过期」）。
        """
        raw = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        return self.server.file_map.get(raw)  # type: ignore[attr-defined]

    def do_HEAD(self) -> None:  # noqa: N802
        path = self._lookup()
        if path is None or not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self._lookup()
        if path is None or not path.is_file():
            self.send_error(404)
            return

        data = path.read_bytes()
        total = len(data)
        rng = self.headers.get("Range")
        support = getattr(self.server, "support_range", True)

        if rng and support:
            match = re.match(r"bytes=(\d+)-(\d*)", rng)
            if not match:
                self.send_error(400)
                return
            start = int(match.group(1))
            if start >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.end_headers()
                return
            body = data[start:]
            self.send_response(206)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Range", f"bytes {start}-{total - 1}/{total}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self._write(body, start)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(total))
        self.end_headers()
        self._write(data, 0)

    def _write(self, body: bytes, offset: int) -> None:
        choke = getattr(self.server, "choke_after", 0)
        if choke and len(body) > choke:
            try:
                self.wfile.write(body[:choke])
                self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True
            return
        try:
            self.wfile.write(body)
        except OSError:
            pass


class RangeFileServer:
    """把若干文件暴露成「直链」，并支持 Range / 断流两种模拟。"""

    def __init__(
        self,
        files: dict[str, Path],
        *,
        support_range: bool = True,
        choke_after: int = 0,
    ) -> None:
        self._files = {("/" + k.lstrip("/")): v for k, v in files.items()}
        self.support_range = support_range
        self.choke_after = choke_after
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def __enter__(self) -> "RangeFileServer":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> "RangeFileServer":
        handler = type("_BoundHandler", (_RangeHandler,), {})
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        httpd.daemon_threads = True
        httpd.file_map = self._files  # type: ignore[attr-defined]
        httpd.support_range = self.support_range  # type: ignore[attr-defined]
        httpd.choke_after = self.choke_after  # type: ignore[attr-defined]

        self._httpd = httpd
        self.port = int(httpd.server_address[1])
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def url_for(self, name: str) -> str:
        """生成直链（对文件名做百分号编码，模拟真实直链的行为）。"""
        quoted = urllib.parse.quote(name.lstrip("/"), safe="")
        return f"http://127.0.0.1:{self.port}/{quoted}"


# ------------------------------------------------------------------ 构造器

def make_secrets(tmp: Path) -> SecretStore:
    """显式注入假后端的凭据库（测试用，**不**走真实 DPAPI）。"""
    return SecretStore(tmp / "secrets.enc", backend=InsecureDevBackend())


def make_config(
    tmp: Path,
    *,
    groups: Iterable[str] = ("123456789",),
    include: Iterable[str] = (r".*\.(pdf|xlsx)$",),
    exclude: Iterable[str] = (),
    upload_root: Path | None = None,
    split_by_group: bool = True,
    keep_local_days: int = 0,
) -> AppConfig:
    return AppConfig(
        groups=list(groups),
        filters=FilterConfig(
            include=list(include),
            exclude=list(exclude),
            extensions=[],
            min_size_mb=0.0,
            max_size_mb=0.0,
        ),
        napcat=NapCatConfig(autostart=False),
        upload=UploadConfig(
            adapter="local",
            remote_root="QQ群备份",
            local_root=str(upload_root or (tmp / "fake_netdisk")),
            split_by_group=split_by_group,
            verify_after_upload=True,
        ),
        monitor=MonitorConfig(
            poll_interval_sec=60,
            jitter_sec=0,
            download_concurrency=1,
            max_retries=2,
            retry_backoff_sec=0,
            keep_local_days=keep_local_days,
            max_file_mb=0.0,
            min_free_disk_gb=0.0,
            recursive_folders=True,
            page_size=50,
        ),
        temp_dir=str(tmp / "tmp"),
    )
