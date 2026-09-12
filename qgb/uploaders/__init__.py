"""上传适配器注册表。

新增后端只需要：
  1. 实现 :class:`qgb.uploaders.base.Uploader`
  2. 在这里 ``register()`` 一次
流水线无需任何改动。
"""

from __future__ import annotations

from typing import Type

from ..config import UploadConfig
from ..errors import ConfigError
from ..secrets import SecretStore
from .base import Uploader, UploadResult
from .local import LocalUploader
from .webdav import WebDAVUploader

__all__ = [
    "Uploader",
    "UploadResult",
    "LocalUploader",
    "WebDAVUploader",
    "ADAPTERS",
    "register",
    "available_adapters",
    "build_uploader",
]

ADAPTERS: dict[str, Type[Uploader]] = {
    WebDAVUploader.name: WebDAVUploader,
    LocalUploader.name: LocalUploader,
}


def register(cls: Type[Uploader]) -> Type[Uploader]:
    """装饰器形式注册新适配器。"""
    if not cls.name:
        raise ValueError("适配器必须有非空的 name")
    ADAPTERS[cls.name] = cls
    return cls


def available_adapters() -> list[tuple[str, str]]:
    """返回 ``[(id, 显示名), ...]``，供 GUI 下拉框使用。"""
    return [(key, cls.display_name or key) for key, cls in sorted(ADAPTERS.items())]


def build_uploader(cfg: UploadConfig, secrets: SecretStore) -> Uploader:
    cls = ADAPTERS.get(cfg.adapter)
    if cls is None:
        raise ConfigError(
            f"未知的上传适配器：{cfg.adapter}",
            hint=f"可选值：{', '.join(sorted(ADAPTERS))}",
        )
    return cls(cfg, secrets)
