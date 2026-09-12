"""从 NapCat WebUI 获取登录二维码。

**本模块的端点已按 NapCat 源码核对**（v4.18.x 的
``packages/napcat-webui-backend/src/router/{auth,QQLogin}.ts``）：

===============================  ==========================================
``POST /api/auth/login``         请求体 ``{"token": "<webui.json 里的 token>"}``
                                 → ``{code: 0, data: {Credential: "<base64>"}}``
``POST /api/QQLogin/GetQQLoginQrcode``
                                 头部 ``Authorization: Bearer <Credential>``
                                 → ``{code: 0, data: {qrcode: "<登录URL>"}}``
``POST /api/QQLogin/CheckLoginStatus``
                                 → ``{code: 0, data: {isLogin, isOffline, loginError}}``
===============================  ==========================================

⚠️ 关键点：``GetQQLoginQrcode`` 返回的**不是图片**，而是一个**登录 URL**
（形如 ``https://txz.qq.com/p?k=...``）。NapCat 官方前端在浏览器里把这个
URL 渲染成二维码；我们在本地用 ``segno`` 生成 PNG，于是二维码可以直接显示
在自己的界面里，不必打开浏览器。

二维码本身不含账号信息，它只是把「手机 QQ 确认登录」这个动作带到本机。
真正敏感的 WebUI token 与 Credential 由调用方从加密凭据库取用，绝不落日志。
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import requests

__all__ = [
    "QrCode",
    "LoginStatus",
    "fetch_qrcode",
    "check_login",
    "webui_url",
    "generate_qr_png",
]

log = logging.getLogger(__name__)

#: 登录取凭据（已核对源码）
AUTH_PATH = "/api/auth/login"
#: 取二维码（已核对源码）
QR_PATH = "/api/QQLogin/GetQQLoginQrcode"
#: 查登录状态（已核对源码）
STATUS_PATH = "/api/QQLogin/CheckLoginStatus"

#: NapCat 返回的错误串 → 给部署方可操作的提示
_ERROR_HINTS = {
    "token is empty": "WebUI 令牌为空：请填写，或点「从 NapCat 配置自动读取」。",
    "token is invalid": "WebUI 令牌不正确：请重新读取 webui.json 里的 token。",
    "login rate limit": "登录尝试过于频繁：请等 1 分钟再试。",
    "server token not initialized": "NapCat 尚未完成启动：请稍等几秒后重试。",
    "qq is logined": "QQ 已经处于登录状态，无需再扫码。",
    "qrcode get error": "NapCat 还没生成二维码：请稍等几秒再点一次「获取二维码」。",
}

#: 本地二维码文件的候选名（部分版本会把二维码直接落盘）
_LOCAL_QR_GLOBS = ("*qrcode*.png", "*QRCode*.png", "*qrcode*.jpg")


@dataclass(slots=True)
class QrCode:
    """二维码获取结果。"""

    #: 可直接显示的 PNG 字节
    png_bytes: bytes | None = None
    #: 二维码内容（QQ 登录 URL）
    qrcode_url: str = ""
    #: 数据来源（用于排查）
    source: str = ""
    #: 失败原因（人类可读，不含凭据）
    error: str = ""
    #: 给部署方的操作建议
    hint: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.png_bytes)

    def save(self, path: Path | str) -> Path | None:
        if not self.png_bytes:
            return None
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.png_bytes)
        return path


@dataclass(slots=True)
class LoginStatus:
    is_login: bool = False
    is_offline: bool = False
    login_error: str = ""
    qrcode_url: str = ""
    reachable: bool = False
    message: str = ""

    @property
    def category(self) -> str:
        if not self.reachable:
            return "unknown"
        if self.is_login:
            return "online"
        if self.is_offline:
            return "expired"
        return "offline"


def webui_url(webui_base: str, token: str = "") -> str:
    """「在浏览器中打开登录页」用的地址（仅在本机打开，不外发）。"""
    base = (webui_base or "").rstrip("/")
    if not base:
        return ""
    return f"{base}/webui?token={token}" if token else f"{base}/webui"


# ------------------------------------------------------------------ 内部

def _friendly_error(raw: str) -> tuple[str, str]:
    """把 NapCat 的错误串翻译成「原因 + 建议」。"""
    text = (raw or "").strip()
    low = text.lower()
    for key, hint in _ERROR_HINTS.items():
        if key in low:
            return text, hint
    if not text:
        return "未知错误", "请确认 NapCat 已启动、WebUI 地址与令牌正确。"
    return text, "请点「打开 NapCat 网页版」确认组件状态。"


def _post(session: requests.Session, url: str, *, headers=None, json_body=None,
          timeout=(6.0, 20.0)):
    try:
        return session.post(url, headers=headers, json=json_body, timeout=timeout)
    except requests.RequestException as exc:
        raise ConnectionError(exc.__class__.__name__) from exc


def _password_hash(token: str) -> str:
    """按 NapCat 的算法生成密码哈希。

    **这是实测核对出来的，不是猜的。** NapCat v4.x 的
    ``helper/SignToken.ts`` 里::

        public static generatePasswordHash (password: string): string {
          return crypto.createHash('sha256')
                       .update(password + '.napcat').digest().toString('hex');
        }

    而 ``api/Auth.ts`` 的登录处理取的是 ``req.body.hash``（**不是 token**）::

        const { hash, totpCode } = req.body;
        if (isEmpty(hash)) return sendError(res, 'token is empty');
        if (!AuthHelper.comparePasswordHash(initialToken, hash)) ...

    所以正确请求体是 ``{"hash": sha256(token + ".napcat")}``。
    发 ``{"token": ...}`` 会被回一句让人困惑的
    ``token is empty``（服务端其实收到了，只是字段名不对）。
    """
    return hashlib.sha256(f"{token}.napcat".encode("utf-8")).hexdigest()


def _get_credential(
    session: requests.Session, base: str, token: str, timeout
) -> tuple[str, str]:
    """用 WebUI 令牌换取 Credential。

    返回 ``(credential, problem)``，其中 ``problem`` 为：

    * ``""``            成功
    * ``"unreachable"`` 连不上 NapCat（**不能**说成「令牌无效」——那会
      把用户引向错误的排查方向）
    * ``"invalid"``     连上了但令牌被拒

    先按当前版本的 ``hash`` 字段发；若被拒再退回旧版的 ``token`` 字段，
    以兼容不同 NapCat 版本。
    """
    if not token:
        return "", ""

    payloads = ({"hash": _password_hash(token)}, {"token": token})
    last_problem = "invalid"

    for payload in payloads:
        try:
            resp = _post(
                session, f"{base}{AUTH_PATH}", json_body=payload, timeout=timeout
            )
        except ConnectionError:
            return "", "unreachable"

        if resp.status_code == 401:
            last_problem = "invalid"
            continue
        if resp.status_code != 200:
            return "", ("unreachable" if resp.status_code >= 500 else "invalid")

        try:
            body = resp.json()
        except ValueError:
            last_problem = "invalid"
            continue

        if not isinstance(body, dict):
            last_problem = "invalid"
            continue

        code = body.get("code")
        if code not in (0, None):
            raw = str(body.get("message") or "")
            low = raw.lower()
            if "not initialized" in low:
                return "", "unreachable"
            # 字段名不对时 NapCat 会回 "token is empty" —— 换一种载荷重试
            last_problem = "invalid"
            continue

        data = body.get("data")
        if isinstance(data, dict):
            cred = data.get("Credential") or data.get("credential") or ""
            if cred:
                return str(cred), ""
        if isinstance(data, str) and data:
            return data, ""
        last_problem = "invalid"

    return "", last_problem


def _credential_problem(problem: str) -> QrCode:
    """把取凭据阶段的问题翻译成给用户的提示。"""
    if problem == "unreachable":
        return QrCode(
            error="无法连接 NapCat",
            hint="请确认已点「启动组件」，并且 WebUI 地址与端口正确（默认 127.0.0.1:6099）。",
        )
    return QrCode(
        error="WebUI 令牌无效",
        hint="请点「从 NapCat 配置自动读取」，或手动填写 NapCat 的 webui.json 中的 token。",
    )


def _generate_png(content: str, *, scale: int = 6) -> bytes | None:
    """把二维码内容渲染成 PNG（**本地生成**，不经过任何第三方服务）。"""
    if not content:
        return None
    try:
        import io

        import segno
    except ImportError:
        log.warning("未安装 segno，无法在界面显示二维码（可改用「打开网页版」）")
        return None

    try:
        qr = segno.make(content, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=scale, border=2, dark="#0F1625", light="#FFFFFF")
        return buf.getvalue()
    except Exception as exc:
        log.warning("二维码生成失败：%s", type(exc).__name__)
        return None


def _find_local_qrcode(napcat_dir: Path | str | None) -> bytes | None:
    """回退：部分版本会把二维码直接写到安装目录。"""
    if not napcat_dir:
        return None
    base = Path(napcat_dir)
    if not base.is_dir():
        return None
    for pattern in _LOCAL_QR_GLOBS:
        for candidate in base.rglob(pattern):
            try:
                if candidate.stat().st_size > 0:
                    return candidate.read_bytes()
            except OSError:
                continue
    return None


# ------------------------------------------------------------------ 对外

def check_login(webui_base: str, token: str = "", *, timeout=(6.0, 20.0)) -> LoginStatus:
    """查询 QQ 登录状态（走已核对的 ``CheckLoginStatus`` 接口）。"""
    base = (webui_base or "").rstrip("/")
    if not base:
        return LoginStatus(message="未配置 NapCat WebUI 地址")

    session = requests.Session()
    session.headers.update({"User-Agent": "qgb/0.1 (login)"})

    credential, problem = _get_credential(session, base, token, timeout)
    if problem:
        status = LoginStatus(message={
            "unreachable": "无法连接 NapCat",
            "invalid": "WebUI 令牌无效",
        }.get(problem, problem))
        return status

    headers = {"Authorization": f"Bearer {credential}"} if credential else {}
    try:
        resp = _post(session, f"{base}{STATUS_PATH}", headers=headers, timeout=timeout)
    except ConnectionError as exc:
        return LoginStatus(message=f"无法连接 NapCat（{exc}）")

    if resp.status_code == 401:
        return LoginStatus(message="WebUI 令牌无效（401）")
    if resp.status_code != 200:
        return LoginStatus(message=f"接口返回 {resp.status_code}")

    try:
        body = resp.json()
    except ValueError:
        return LoginStatus(message="返回内容不是 JSON")

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raw = str(body.get("message") if isinstance(body, dict) else body)
        reason, hint = _friendly_error(raw)
        return LoginStatus(message=f"{reason}（{hint}）")

    login_error = str(data.get("loginError") or "")
    if data.get("isLogin"):
        message = "已登录"
    elif data.get("isOffline"):
        message = "登录已失效，请重新扫码"
    elif login_error:
        # NapCat 会给出很具体的原因（例如「二维码已过期，请刷新」），
        # 直接透传给用户比只说「未登录」有用得多
        message = f"未登录（{login_error}）"
    else:
        message = "未登录"

    return LoginStatus(
        is_login=bool(data.get("isLogin")),
        is_offline=bool(data.get("isOffline")),
        login_error=login_error,
        qrcode_url=str(data.get("qrcodeurl") or ""),
        reachable=True,
        message=message,
    )


def fetch_qrcode(
    webui_base: str,
    token: str = "",
    *,
    timeout=(6.0, 25.0),
    napcat_dir: Path | str | None = None,
) -> QrCode:
    """获取登录二维码并**在本地渲染成 PNG**。"""
    base = (webui_base or "").rstrip("/")
    if not base:
        return QrCode(
            error="未配置 NapCat WebUI 地址",
            hint="请在「QQ 登录」页填写 WebUI 地址（默认 127.0.0.1:6099）。",
        )

    session = requests.Session()
    session.headers.update({"User-Agent": "qgb/0.1 (qr)"})

    credential, problem = _get_credential(session, base, token, timeout)
    if problem:
        return _credential_problem(problem)

    headers = {"Authorization": f"Bearer {credential}"} if credential else {}
    try:
        resp = _post(session, f"{base}{QR_PATH}", headers=headers, timeout=timeout)
    except ConnectionError as exc:
        return QrCode(
            error=f"无法连接 NapCat（{exc}）",
            hint="请确认已点「启动组件」，且 WebUI 地址正确。",
        )

    if resp.status_code == 401:
        return QrCode(error="WebUI 令牌无效（401）", hint="请重新读取或填写 WebUI 令牌。")

    if resp.status_code != 200:
        return QrCode(
            error=f"接口返回 {resp.status_code}",
            hint="请稍后重试，或点「打开 NapCat 网页版」查看组件状态。",
        )

    try:
        body = resp.json()
    except ValueError:
        return QrCode(error="返回内容不是 JSON", hint="可能是版本差异，请更新 NapCat。")

    # NapCat 失败时返回 {code: 非0, message: "..."}
    code = body.get("code") if isinstance(body, dict) else None
    if code not in (0, None):
        reason, hint = _friendly_error(str(body.get("message") or body.get("msg") or ""))
        return QrCode(error=reason, hint=hint)

    data = body.get("data") if isinstance(body, dict) else None
    qr_content = ""
    if isinstance(data, dict):
        qr_content = str(data.get("qrcode") or data.get("qrcodeurl") or "")
    elif isinstance(data, str):
        qr_content = data

    if qr_content:
        png = _generate_png(qr_content)
        if png:
            log.info("二维码获取成功（本地渲染自登录 URL）")
            return QrCode(png_bytes=png, qrcode_url=qr_content, source="webui+segno")
        return QrCode(
            qrcode_url=qr_content,
            source="webui-url-only",
            error="已取到二维码内容，但无法渲染成图片",
            hint="请执行 pip install segno 后重启程序；或点「打开 NapCat 网页版」扫码。",
        )

    # 兜底：部分版本把二维码图片落在安装目录
    local = _find_local_qrcode(napcat_dir)
    if local:
        log.info("二维码获取成功（来源：安装目录内的文件）")
        return QrCode(png_bytes=local, source="local-file")

    reason, hint = _friendly_error(str(body.get("message") if isinstance(body, dict) else ""))
    return QrCode(error=reason or "未返回二维码", hint=hint)


def generate_qr_png(content: str, *, scale: int = 6) -> bytes | None:
    """把任意内容渲染成二维码 PNG（供测试与复用）。"""
    return _generate_png(content, scale=scale)
