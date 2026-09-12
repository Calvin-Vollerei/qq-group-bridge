"""最小 WebDAV 服务端（仅测试用）。

只实现本项目用到的四个方法：``MKCOL`` / ``PUT`` / ``HEAD`` / ``PROPFIND``。
用途是**在没有真实 OpenList 的情况下，离线验证 WebDAV 适配器**——
包括中文文件名编码、逐级建目录、Basic 认证失败分支、上传后回读校验。
"""

from __future__ import annotations

import base64
import http.server
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["FakeWebDAVServer"]


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeOpenListDAV/1.0"
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------- 工具

    def _norm(self) -> str:
        path = urllib.parse.unquote(self.path)
        return path.rstrip("/") or "/"

    def _authorized(self) -> bool:
        if not getattr(self.server, "require_auth", True):
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            raw = base64.b64decode(header[6:]).decode("utf-8", "replace")
        except Exception:
            return False
        user, _, pwd = raw.partition(":")
        return (user, pwd) == (self.server.username, self.server.password)

    def _send(self, code: int, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.send_response(code)
        merged = dict(headers or {})
        # HEAD 需要显式声明资源长度，因此允许调用方覆盖；避免重复 header
        merged.setdefault("Content-Length", str(len(body)))
        for k, v in merged.items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            try:
                self.wfile.write(body)
            except OSError:
                pass

    def _deny(self) -> None:
        self._send(401, b"", {"WWW-Authenticate": 'Basic realm="fake"'})

    def log_message(self, *args) -> None:  # 静音
        return

    # -------------------------------------------------- 方法

    def do_MKCOL(self) -> None:  # noqa: N802
        if not self._authorized():
            self._deny()
            return
        path = self._norm()
        if path in self.server.dirs:  # type: ignore[attr-defined]
            # 各家实现对"目录已存在"的返回码不一致：
            # WebDAV 规范是 405，而 OpenList 对挂载点返回 **403**。
            self._send(getattr(self.server, "mkcol_existing_code", 405))  # type: ignore[attr-defined]
            return
        self.server.dirs.add(path)  # type: ignore[attr-defined]
        self._send(201)

    def do_PUT(self) -> None:  # noqa: N802
        if not self._authorized():
            self._deny()
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.files[self._norm()] = body  # type: ignore[attr-defined]
        self._send(201)

    def do_HEAD(self) -> None:  # noqa: N802
        if not self._authorized():
            self._deny()
            return
        path = self._norm()
        data = self.server.files.get(path)  # type: ignore[attr-defined]
        if data is None:
            self._send(404)
            return
        self._send(200, headers={"Content-Length": str(len(data))})

    def do_PROPFIND(self) -> None:  # noqa: N802
        """如实模拟存在性判断。

        ⚠️ **不能对任何路径都返回 207。** 真实 WebDAV 对不存在的路径返回
        **404**（在 OpenList 上实测：``/QQ群备份/`` → 404，而
        ``/baidu/QQ群备份/`` → 207）。早先这里一律 207，于是"先探测是否存在、
        再决定要不要 MKCOL"的逻辑永远认为目录已存在、什么都不建 ——
        测试通过但真机会漏建目录。
        """
        if not self._authorized():
            self._deny()
            return

        path = self._norm()
        # WebDAV 的根（base_path 本身）永远存在，任何实现都是这样；
        # 别忘了它，否则连连通性自检都会被判成 404。
        base = str(getattr(self.server, "base_path", "/dav")).rstrip("/") or "/"
        exists = (
            path in self.server.dirs          # type: ignore[attr-defined]
            or path in self.server.files      # type: ignore[attr-defined]
            or path in ("/", base)
        )
        if not exists:
            self._send(404)
            return

        body = b'<?xml version="1.0"?><D:multistatus xmlns:D="DAV:"></D:multistatus>'
        self._send(207, body, {"Content-Type": 'application/xml; charset="utf-8"'})

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._deny()
            return
        data = self.server.files.get(self._norm())  # type: ignore[attr-defined]
        if data is None:
            self._send(404)
            return
        self._send(200, data)


@dataclass
class FakeWebDAVServer:
    """内存版 WebDAV，附带断言用的状态。"""

    username: str = "admin"
    password: str = "secret"
    require_auth: bool = True
    base_path: str = "/dav"
    #: MKCOL 遇到已存在目录时返回什么。规范是 405；OpenList 对挂载点返回 403。
    mkcol_existing_code: int = 405
    dirs: set[str] = field(default_factory=lambda: {"/"})
    files: dict[str, bytes] = field(default_factory=dict)
    _httpd: http.server.ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    port: int = 0

    # -------------------------------------------------- 生命周期

    def __enter__(self) -> "FakeWebDAVServer":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> "FakeWebDAVServer":
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        httpd.username = self.username  # type: ignore[attr-defined]
        httpd.password = self.password  # type: ignore[attr-defined]
        httpd.require_auth = self.require_auth  # type: ignore[attr-defined]
        httpd.dirs = self.dirs  # type: ignore[attr-defined]
        httpd.files = self.files  # type: ignore[attr-defined]
        httpd.base_path = self.base_path  # type: ignore[attr-defined]
        httpd.mkcol_existing_code = self.mkcol_existing_code  # type: ignore[attr-defined]

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

    # -------------------------------------------------- 访问

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}{self.base_path}"

    def file_at(self, remote_path: str) -> bytes | None:
        """按远端路径取内容（自动补 base_path）。"""
        key = f"{self.base_path}/{remote_path.lstrip('/')}".rstrip("/")
        return self.files.get(key)

    def has_dir(self, remote_path: str) -> bool:
        key = f"{self.base_path}/{remote_path.strip('/')}".rstrip("/")
        return key in self.dirs


def local_path_of(server: FakeWebDAVServer, remote_path: str) -> Path:
    """便于断言：返回一个占位 Path（内容请用 ``file_at``）。"""
    return Path(f"{server.base_path}/{remote_path.lstrip('/')}")
