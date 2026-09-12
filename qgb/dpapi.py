"""Windows DPAPI 封装（ctypes 直调 crypt32）。

为什么用 DPAPI 而不是自研 AES：
  * 密钥由 Windows 从**当前用户登录凭据**派生并托管，我们不持有任何主密钥
  * 密文绑定「当前用户 + 本机」——把 ``secrets.enc`` 拷到别的电脑/别的用户下**无法解密**
  * 天然规避「自研加密 + 硬编码密钥」这类最危险的做法

仅支持 Windows（目标平台）。其他平台抛出 :class:`PlatformUnsupported`，
测试可通过注入假后端绕开（见 ``qgb.secrets.SecretStore``）。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from .errors import CryptoError, PlatformUnsupported

__all__ = ["available", "protect", "unprotect", "CryptoError", "PlatformUnsupported"]

IS_WINDOWS = sys.platform == "win32"

#: 供 CryptProtectData 使用的附加熵（与 APP_ID 绑定，避免跨程序误用密文）
DEFAULT_ENTROPY = b"qgb.dpapi.v1"

_CRYPTPROTECT_UI_FORBIDDEN = 0x01

if IS_WINDOWS:  # pragma: no cover - 平台相关分支

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL

    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    _kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    _kernel32.LocalFree.restype = wintypes.HLOCAL

    def _to_blob(data: bytes) -> _DataBlob:
        """把 bytes 拷进一个 DATA_BLOB（buffer 的生命周期由调用方持有）。"""
        if not data:
            raise ValueError("_to_blob 不接受空数据；空熵请直接传 None")
        buf = ctypes.create_string_buffer(data, len(data))
        return _DataBlob(
            len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte))
        )

    def _from_blob(blob: _DataBlob) -> bytes:
        """读取输出 blob 并释放其由系统分配的缓冲区。"""
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            if blob.pbData:
                _kernel32.LocalFree(ctypes.cast(blob.pbData, wintypes.HLOCAL))

    def _last_error() -> str:
        err = ctypes.get_last_error()
        return f"WinError {err}: {ctypes.FormatError(err).strip()}"


def available() -> bool:
    """当前平台是否可用 DPAPI。"""
    return IS_WINDOWS


def protect(data: bytes, *, entropy: bytes = DEFAULT_ENTROPY, description: str = "qgb") -> bytes:
    """加密任意字节串，返回不可跨用户/跨机器解密的密文。"""
    if not IS_WINDOWS:
        raise PlatformUnsupported("DPAPI 仅在 Windows 上可用")

    in_blob = _to_blob(bytes(data))
    ent_blob = _to_blob(entropy) if entropy else None
    out_blob = _DataBlob()

    ok = _crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        description,
        ctypes.byref(ent_blob) if ent_blob is not None else None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise CryptoError(f"加密失败（{_last_error()}）")
    return _from_blob(out_blob)


def unprotect(data: bytes, *, entropy: bytes = DEFAULT_ENTROPY) -> bytes:
    """解密由 :func:`protect` 生成的密文。"""
    if not IS_WINDOWS:
        raise PlatformUnsupported("DPAPI 仅在 Windows 上可用")

    in_blob = _to_blob(bytes(data))
    ent_blob = _to_blob(entropy) if entropy else None
    out_blob = _DataBlob()

    ok = _crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob) if ent_blob is not None else None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        # 最常见的两种原因：换了电脑/用户，或文件被篡改
        raise CryptoError(
            "解密失败：凭据与本机当前 Windows 用户绑定，"
            "更换电脑或用户后需要重新授权"
        )
    return _from_blob(out_blob)
