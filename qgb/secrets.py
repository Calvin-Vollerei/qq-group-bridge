"""加密凭据库（``secrets.enc``）。

职责边界（很重要）：
  * ``config.json``   —— 非敏感配置，可随分发包一起走
  * ``secrets.enc``   —— **唯一**存放凭据的地方，DPAPI 加密，绝不入包、绝不入 git

凭据键集中定义在本模块，避免散落各处的字符串常量导致「漏加密」。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from . import dpapi
from .errors import CredentialError, PlatformUnsupported
from .redact import clear_registered, mask_id, mask_secret, register_secret
from .version import DATA_DIR_NAME

__all__ = [
    "SecretStore",
    "DpapiBackend",
    "InsecureDevBackend",
    "default_data_dir",
    "default_store_path",
    "SECRET_KEYS",
    "KEY_NETDISK_WEBDAV_USERNAME",
    "KEY_NETDISK_WEBDAV_PASSWORD",
    "KEY_NETDISK_TOKEN",
    "KEY_QQ_ONEBOT_TOKEN",
    "KEY_NAPCAT_WEBUI_TOKEN",
]

# ------------------------------------------------------------------ 凭据键

KEY_NETDISK_TOKEN = "netdisk.token"
KEY_NETDISK_WEBDAV_USERNAME = "netdisk.webdav_username"
KEY_NETDISK_WEBDAV_PASSWORD = "netdisk.webdav_password"
KEY_QQ_ONEBOT_TOKEN = "qq.onebot_token"
KEY_NAPCAT_WEBUI_TOKEN = "napcat.webui_token"

#: 键 -> (中文标签, 是否按 ID 方式遮蔽)
SECRET_KEYS: dict[str, tuple[str, bool]] = {
    KEY_NETDISK_TOKEN: ("网盘访问令牌", False),
    KEY_NETDISK_WEBDAV_USERNAME: ("OpenList 用户名", True),
    KEY_NETDISK_WEBDAV_PASSWORD: ("OpenList 密码", False),
    KEY_QQ_ONEBOT_TOKEN: ("OneBot 访问令牌", False),
    KEY_NAPCAT_WEBUI_TOKEN: ("NapCat WebUI 令牌", False),
}

_MAGIC = b"QGB1"
_MAX_PAYLOAD = 256 * 1024  # 凭据库不该超过 256KB，防御异常文件


# ------------------------------------------------------------------ 后端

class Backend(Protocol):
    """加解密后端。"""

    name: str

    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, data: bytes) -> bytes: ...


class DpapiBackend:
    """生产后端：Windows DPAPI，绑定当前用户 + 本机。"""

    name = "Windows DPAPI（当前用户 + 本机）"

    def protect(self, data: bytes) -> bytes:
        return dpapi.protect(data)

    def unprotect(self, data: bytes) -> bytes:
        return dpapi.unprotect(data)


class InsecureDevBackend:
    """**仅用于开发/测试**的假后端，绝不可用于真实部署。

    用固定密钥做流式异或，只保证「文件里不是明文」，不提供任何真实安全性。
    构造时会打印醒目告警（每个进程只提示一次，避免刷屏）。
    """

    name = "⚠ 不安全开发后端（禁止用于真实部署）"
    _KEY = b"qgb-insecure-dev-backend-key-do-not-ship"
    _warned = False

    def __init__(self) -> None:
        import logging

        if not InsecureDevBackend._warned:
            InsecureDevBackend._warned = True
            logging.getLogger(__name__).warning(
                "已启用不安全开发后端：凭据仅做了混淆，未真正加密。"
                "真实部署必须使用 Windows DPAPI。"
            )

    def _xor(self, data: bytes) -> bytes:
        key = self._KEY
        n = len(key)
        return bytes(b ^ key[i % n] for i, b in enumerate(data))

    def protect(self, data: bytes) -> bytes:
        return self._xor(data)

    def unprotect(self, data: bytes) -> bytes:
        return self._xor(data)


def default_backend() -> Backend:
    """按平台选择后端；非 Windows 上仅当显式开启开发开关才降级。"""
    if dpapi.available():
        return DpapiBackend()
    if os.environ.get("QGB_ALLOW_INSECURE_SECRETS") == "1":
        return InsecureDevBackend()
    raise PlatformUnsupported(
        "凭据加密依赖 Windows DPAPI。"
        "若你正在非 Windows 环境做开发，可设置环境变量 "
        "QGB_ALLOW_INSECURE_SECRETS=1 使用仅测试用的假后端。"
    )


# ------------------------------------------------------------------ 路径

def default_data_dir() -> Path:
    """应用数据目录。

    实际位置由 :mod:`qgb.paths` 决定：它会**实际探测可写性**后在
    「环境变量 → 便携模式 → %LOCALAPPDATA% → 临时目录 → 程序目录」
    之间挑选，避免在受限账户上直接崩溃。
    """
    from .paths import resolve_data_dir

    return resolve_data_dir()


def default_store_path() -> Path:
    return default_data_dir() / "secrets.enc"


# ------------------------------------------------------------------ 存储

class SecretStore:
    """DPAPI 加密的键值凭据库。

    用法::

        store = SecretStore()
        store.set(KEY_NETDISK_WEBDAV_PASSWORD, "xxx")
        store.get(KEY_NETDISK_WEBDAV_PASSWORD)
    """

    def __init__(self, path: Path | str | None = None, backend: Backend | None = None) -> None:
        self.path = Path(path) if path is not None else default_store_path()
        self._backend: Backend | None = backend
        self._items: dict[str, str] = {}
        self._loaded = False

    # -------------------------------------------------- 内部

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = default_backend()
        return self._backend

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._items = {}
        if self.path.exists():
            try:
                raw = self.path.read_bytes()
            except OSError as exc:
                raise CredentialError(f"无法读取凭据文件：{exc}") from exc

            if not raw.startswith(_MAGIC):
                raise CredentialError(
                    "凭据文件格式不正确（缺少文件头）。"
                )
            payload = raw[len(_MAGIC):]
            if len(payload) > _MAX_PAYLOAD:
                raise CredentialError("凭据文件异常偏大，已拒绝加载。")
            try:
                plain = self.backend.unprotect(payload)
            except Exception as exc:  # dpapi.CryptoError / 其他后端异常
                raise CredentialError(str(exc)) from exc

            try:
                data = json.loads(plain.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CredentialError("凭据文件内容损坏，请重新授权。") from exc

            items = data.get("items") if isinstance(data, dict) else None
            if isinstance(items, dict):
                self._items = {
                    str(k): str(v) for k, v in items.items() if isinstance(v, str)
                }

        # 登记到脱敏表：任何日志里出现明文凭据都会被替换
        for value in self._items.values():
            register_secret(value)

        self._loaded = True

    def _flush(self) -> None:
        data = {"version": 1, "items": self._items}
        plain = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        cipher = self.backend.protect(plain)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_bytes(_MAGIC + cipher)
        os.replace(tmp, self.path)

        # 收紧权限：仅当前用户可读写
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # -------------------------------------------------- 公共 API

    def get(self, key: str, default: str | None = None) -> str | None:
        self._ensure_loaded()
        value = self._items.get(key, default)
        if value:
            register_secret(value)
        return value

    def require(self, key: str) -> str:
        value = self.get(key)
        if not value:
            label = SECRET_KEYS.get(key, (key, False))[0]
            raise CredentialError(f"缺少凭据：{label}（{key}）。请先完成授权。")
        return value

    def set(self, key: str, value: str) -> None:
        self._ensure_loaded()
        self._items[key] = str(value)
        register_secret(str(value))
        self._flush()

    def set_many(self, values: dict[str, str]) -> None:
        self._ensure_loaded()
        for key, value in values.items():
            self._items[key] = str(value)
            register_secret(str(value))
        self._flush()

    def delete(self, key: str) -> bool:
        self._ensure_loaded()
        existed = key in self._items
        if existed:
            self._items.pop(key, None)
            self._flush()
        return existed

    def clear(self) -> None:
        """一键清除全部凭据（GUI 的「清除本机凭据」按钮）。"""
        self._items = {}
        self._loaded = True
        clear_registered()
        if self.path.exists():
            try:
                self.path.unlink()
            except OSError as exc:
                raise CredentialError(f"无法删除凭据文件：{exc}") from exc

    def keys(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._items)

    def has(self, key: str) -> bool:
        self._ensure_loaded()
        return bool(self._items.get(key))

    def describe(self) -> list[dict[str, Any]]:
        """给「本机保存了什么」界面的只读清单——**只显示遮蔽后的值**。

        刻意不返回：账号昵称、会员状态、容量配额等任何画像信息。
        """
        self._ensure_loaded()
        rows: list[dict[str, Any]] = []
        for key, (label, as_id) in SECRET_KEYS.items():
            value = self._items.get(key)
            if value is None:
                continue
            rows.append(
                {
                    "key": key,
                    "label": label,
                    "present": True,
                    "masked": mask_id(value) if as_id else mask_secret(value),
                }
            )
        return rows

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name,
            "path": str(self.path),
            "exists": self.path.exists(),
            "count": len(self.keys()) if self.path.exists() else 0,
        }
