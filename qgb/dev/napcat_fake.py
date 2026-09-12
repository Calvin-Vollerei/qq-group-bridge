"""最小 NapCat WebUI / OneBot 假服务端（仅测试用）。

目的：**在真实 NapCat 之前，先把接口与鉴权逻辑验证一遍。**

端点严格照 NapCat v4.18.x 源码实现
（``packages/napcat-webui-backend/src/router/{auth,QQLogin}.ts``）::

    POST /api/auth/login                    {token} → {code:0, data:{Credential}}
    POST /api/QQLogin/GetQQLoginQrcode      Bearer  → {code:0, data:{qrcode:<URL>}}
    POST /api/QQLogin/CheckLoginStatus      Bearer  → {code:0, data:{isLogin,isOffline,...}}

外加 OneBot 侧的两个动作，用于验证 ``OneBotClient``::

    POST /get_login_info        → {status:"ok", retcode:0, data:{user_id}}
    POST /get_group_file_list   → {status:"ok", retcode:0, data:{files, folders}}

可以模拟的故障：令牌错误、登录限流、尚未生成二维码、已登录、掉线。
"""

from __future__ import annotations

import base64
import http.server
import json
import threading
from dataclasses import dataclass, field

__all__ = ["FakeNapCat", "DEFAULT_QR_URL"]

#: 真机上不传 file_count 时的默认页大小（约 40-50 条）
_DEFAULT_PAGE = 50

#: 假的二维码内容。真实环境里这是 https://txz.qq.com/p?k=... 形式的登录 URL。
DEFAULT_QR_URL = "https://txz.qq.com/p?k=FAKEKEY1234567890&f=1"


class _Handler(http.server.BaseHTTPRequestHandler):
    server_version = "FakeNapCat/4.18"
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------- 工具

    def log_message(self, *args) -> None:  # 静音
        return

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def _ok(self, data) -> None:
        self._json({"code": 0, "message": "success", "data": data})

    def _err(self, message: str) -> None:
        # NapCat 的 sendError：HTTP 200 + 非 0 code
        self._json({"code": -1, "message": message, "data": None})

    def _read_json(self) -> dict:
        _ = self.headers  # 保留读取语义
        raw = getattr(self, "_raw_body", b"")
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _credential(self) -> str:
        header = self.headers.get("Authorization", "")
        return header[7:].strip() if header.lower().startswith("bearer ") else ""

    def _check_auth(self) -> bool:
        srv = self.server
        if not srv.require_auth:          # type: ignore[attr-defined]
            return True
        cred = self._credential()
        if not cred:
            return False
        try:
            decoded = json.loads(base64.b64decode(cred).decode("utf-8"))
        except Exception:
            return False
        return bool(decoded)      # 假服务端只校验「是个合法 base64 JSON」

    # -------------------------------------------------- 路由

    def do_POST(self) -> None:  # noqa: N802
        # **必须先把请求体读完**，即使这个路由用不到它。
        # 否则残留的 body 会污染 keep-alive 连接上的下一个请求，
        # 表现为莫名其妙的 HTTP 400 / 空响应（这正是踩过的坑）。
        try:
            length = int(self.headers.get("Content-Length") or 0)
            self._raw_body = self.rfile.read(length) if length > 0 else b""
        except (ValueError, OSError):
            self._raw_body = b""

        # 处理器里任何异常都转成 JSON 错误 —— 否则客户端只会看到「空响应」，
        # 排查起来非常费劲。
        try:
            self._route()
        except Exception as exc:  # pragma: no cover - 兜底
            try:
                self._json({"status": "failed", "retcode": 500, "data": None,
                            "message": f"fake server error: {type(exc).__name__}: {exc}"})
            except Exception:
                pass

    def _route(self) -> None:
        path = self.path.split("?")[0].rstrip("/")
        srv = self.server

        if path == "/api/auth/login":
            body = self._read_json()
            if srv.fail_login:                       # type: ignore[attr-defined]
                self._err("server token not initialized")
                return

            # **按真实 NapCat 的契约**：字段名是 ``hash``，值是
            # sha256(token + ".napcat") 的十六进制。
            # （见 napcat-webui-backend/src/{api/Auth.ts, helper/SignToken.ts}）
            submitted = str(body.get("hash") or "")
            if not submitted:
                # 真实的 NapCat 收到 {"token": ...} 时正是回这句
                self._err("token is empty")
                return

            import hashlib

            expected = hashlib.sha256(
                f"{srv.token}.napcat".encode("utf-8")     # type: ignore[attr-defined]
            ).hexdigest()
            if submitted != expected:
                self._err("token is invalid")
                return

            cred = base64.b64encode(
                json.dumps({"HashEncoded": submitted, "CreatedTime": 1}).encode("utf-8")
            ).decode("ascii")
            self._ok({"Credential": cred})
            return

        if path == "/api/QQLogin/GetQQLoginQrcode":
            if not self._check_auth():
                self._json({"code": -1, "message": "Authorization Failed"}, 401)
                return
            if srv.logged_in:                        # type: ignore[attr-defined]
                self._err("QQ Is Logined")
                return
            if not srv.qr_ready:                     # type: ignore[attr-defined]
                self._err("QRCode Get Error")
                return
            self._ok({"qrcode": srv.qr_url})         # type: ignore[attr-defined]
            return

        if path == "/api/QQLogin/CheckLoginStatus":
            if not self._check_auth():
                self._json({"code": -1, "message": "Authorization Failed"}, 401)
                return
            self._ok({
                "isLogin": bool(srv.logged_in),      # type: ignore[attr-defined]
                "isOffline": bool(srv.offline),      # type: ignore[attr-defined]
                "qrcodeurl": srv.qr_url,             # type: ignore[attr-defined]
                "loginError": srv.login_error,       # type: ignore[attr-defined]
            })
            return

        # ---------------- OneBot 动作 ----------------
        if path == "/get_login_info":
            if not srv.logged_in:                    # type: ignore[attr-defined]
                self._json({"status": "failed", "retcode": 100, "message": "未登录"})
                return
            self._json({"status": "ok", "retcode": 0,
                        "data": {"user_id": srv.qq, "nickname": "fake"}})
            return

        # ---------------- 群文件（**真实存在的接口名**）----------------
        #
        # ⚠️ 这里刻意**只实现真机验证过的名字**，并且让错误的名字明确失败。
        #    背景：NapCat 里没有 ``get_group_file_list``，调用它会返回
        #    ``不支持的Api get_group_file_list``。如果假服务端"宽容地"也认这个名字，
        #    测试就会放过一个真机上必然失败的 bug —— 闸门形同虚设。
        #
        # ⚠️ 并且**如实模拟 file_count 截断**：真机上不传 file_count 只会返回
        #    约 40-50 条，历史文件被静默丢弃。假服务端如果总是把全部返回，
        #    就永远测不出这个缺陷。
        if path == "/get_group_root_files":
            body = self._read_json()
            limit = int(body.get("file_count") or _DEFAULT_PAGE)
            self._json({"status": "ok", "retcode": 0,
                        "data": {"files": srv.files[:limit],
                                 "folders": srv.folders}})
            return

        if path == "/get_group_files_by_folder":
            body = self._read_json()
            folder = str(body.get("folder_id") or "")
            limit = int(body.get("file_count") or _DEFAULT_PAGE)
            self._json({"status": "ok", "retcode": 0,
                        "data": {"files": srv.folder_files.get(folder, [])[:limit],
                                 "folders": []}})
            return

        if path == "/get_group_file_list":
            # 真机上就是这个回应
            self._json({"status": "failed", "retcode": 200, "data": None,
                        "message": "不支持的Api get_group_file_list"})
            return

        if path == "/get_group_file_url":
            if srv.url_error:                        # type: ignore[attr-defined]
                # 真机上 PacketBackend 不可用时就是这个回应
                self._json({"status": "failed", "retcode": 400, "data": None,
                            "message": srv.url_error})   # type: ignore[attr-defined]
                return
            fid = str(self._read_json().get("file_id") or "")
            url = srv.file_urls.get(fid)
            if not url:
                self._json({"status": "failed", "retcode": 1, "message": "file not found"})
                return
            self._json({"status": "ok", "retcode": 0, "data": {"url": url}})
            return

        if path == "/get_group_file_download_url":
            # 真机上 NapCat **没有**这个动作 —— 必须如实模拟，
            # 否则测试会以为"备用接口可用"，掩盖 PacketBackend 这个真问题。
            self._json({"status": "failed", "retcode": 200, "data": None,
                        "message": "不支持的Api get_group_file_download_url"})
            return

        self._json({"code": -1, "message": f"unknown route {path}"}, 404)


@dataclass
class FakeNapCat:
    """可配置的假 NapCat。默认「已就绪但未登录」。"""

    token: str = "FAKE-WEBUI-TOKEN"
    qq: str = "10001"

    #: 是否强制要求 Bearer 凭据（用于验证鉴权失败分支）
    require_auth: bool = True
    #: 让 /api/auth/login 失败（模拟组件没起来）
    fail_login: bool = False
    #: 二维码是否已生成
    qr_ready: bool = True
    qr_url: str = DEFAULT_QR_URL
    #: 登录状态
    logged_in: bool = False
    offline: bool = False
    login_error: str = ""

    #: OneBot 侧数据
    files: list[dict] = field(default_factory=list)
    folders: list[dict] = field(default_factory=list)
    #: 子文件夹内容：``{folder_id: [file, ...]}``
    folder_files: dict[str, list[dict]] = field(default_factory=dict)
    file_urls: dict[str, str] = field(default_factory=dict)
    #: 让取直链接口返回指定错误（例如模拟 PacketBackend 不可用）
    url_error: str = ""

    _httpd: http.server.ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None
    port: int = 0

    # -------------------------------------------------- 生命周期

    def __enter__(self) -> "FakeNapCat":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def start(self) -> "FakeNapCat":
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        httpd.daemon_threads = True
        for attr in ("token", "qq", "require_auth", "fail_login", "qr_ready",
                     "qr_url", "logged_in", "offline", "login_error",
                     "files", "folders", "folder_files", "file_urls", "url_error"):
            setattr(httpd, attr, getattr(self, attr))

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
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    #: 设置/读取运行期状态（直接改 httpd 上的属性，保证线程可见）
    def set(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
            if self._httpd is not None:
                setattr(self._httpd, key, value)
