"""上传适配器基类。

设计目标：**把「传到哪个盘」与搬运逻辑彻底解耦**。
搬运流水线只认这个接口，日后换后端（百度直连 / 阿里云盘 / S3 / OneDrive）
只需要新增一个适配器，不动流水线一行代码。
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import UploadConfig
from ..secrets import SecretStore

__all__ = ["Uploader", "UploadResult", "ProgressCb"]

#: (已发送字节, 总字节)
ProgressCb = Callable[[int, int], None]


@dataclass(slots=True)
class UploadResult:
    remote_path: str
    size: int
    verified: bool = False


class Uploader(abc.ABC):
    """所有上传后端的统一契约。"""

    #: 配置里的适配器 id
    name: str = ""
    #: 界面显示名
    display_name: str = ""

    def __init__(self, cfg: UploadConfig, secrets: SecretStore) -> None:
        self.cfg = cfg
        self.secrets = secrets

    # -------------------------------------------------- 必须实现

    @abc.abstractmethod
    def test(self) -> tuple[bool, str]:
        """连通性/授权自检。返回 ``(是否可用, 人类可读说明)``。"""

    @abc.abstractmethod
    def ensure_dir(self, remote_dir: str) -> None:
        """确保远端目录存在（幂等）。"""

    @abc.abstractmethod
    def upload(
        self,
        local_path: Path,
        remote_path: str,
        *,
        on_progress: ProgressCb | None = None,
    ) -> UploadResult:
        """上传单个文件。失败抛 :class:`qgb.errors.UploadError`。"""

    # -------------------------------------------------- 可选实现

    def remote_size(self, remote_path: str) -> int | None:
        """返回远端文件大小；不支持/不存在时返回 ``None``。"""
        return None

    # -------------------------------------------------- 公共辅助

    def build_remote_path(
        self, *, group_id: str, filename: str, folder: str = ""
    ) -> str:
        """按配置拼出远端完整路径。

        ``folder`` 是**上游已经解析好的「群目录名」**（见 :mod:`qgb.naming`）——
        可能是群号、群名、或用户自定义的名字。为空时退回群号，
        保证旧配置与旧调用方行为不变。

        ⚠️ 每一段都要 ``strip("/")``：``remote_root`` 默认带前导斜杠，
        若先 join 再补一个前导 ``/``，就会得到 ``//QQ群备份/...`` 这种
        **双斜杠**路径。WebDAV 多数能容忍，但日志、路径比对与部分
        服务端实现会被它绊到。
        """
        root = (self.cfg.remote_root or "").strip().strip("/")

        segment = ""
        if self.cfg.split_by_group:
            segment = str(folder or group_id or "").strip()

        raw = (root, segment, filename)
        parts = [str(p).strip("/") for p in raw if p]
        parts = [p for p in parts if p]
        return "/" + "/".join(parts)

    @staticmethod
    def _split_dir(remote_path: str) -> tuple[str, str]:
        remote_path = remote_path.replace("\\", "/")
        idx = remote_path.rfind("/")
        if idx <= 0:
            return "/", remote_path.lstrip("/")
        return remote_path[:idx], remote_path[idx + 1:]

    def describe_target(self) -> str:
        """给 UI 显示的目标描述（**不含任何凭据**）。"""
        return self.cfg.remote_root or "/"
