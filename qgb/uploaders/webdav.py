"""WebDAV 上传适配器 —— 面向 **OpenList**（推荐方案）。

为什么经 OpenList 中转：
  * 百度直连 API 是所有环节里最容易被限流/失效的一段；
    把它换成标准 WebDAV 后，后端换盘（百度/阿里/OneDrive/本地）
    **完全不用改我们的代码**，只在 OpenList 网页端配置
  * WebDAV 是稳定公开协议，PUT + MKCOL 足够表达上传语义
  * OpenList 支持 2GB 以上大文件分片（受其后端驱动能力约束，
    百度驱动本身有单文件上限，见项目 README 的已知坑）
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth

from ..errors import UploadError
from ..secrets import (
    KEY_NETDISK_WEBDAV_PASSWORD,
    KEY_NETDISK_WEBDAV_USERNAME,
)
from ..utils import human_size
from .base import ProgressCb, UploadResult, Uploader

__all__ = ["WebDAVUploader"]

log = logging.getLogger(__name__)

#: 这些状态码在 MKCOL 时表示「目录已存在」，属于正常情况
_MKCOL_OK_ALREADY = (301, 302, 405)


class _ProgressReader:
    """把文件对象包一层，边读边回报进度（requests 会按块读它）。"""

    def __init__(self, fh, total: int, cb: ProgressCb | None) -> None:
        self._fh = fh
        self._total = total
        self._cb = cb
        self._sent = 0
        self._last = 0.0

    def __len__(self) -> int:
        return self._total

    def read(self, size: int = -1) -> bytes:
        block = self._fh.read(size)
        if block:
            self._sent += len(block)
            now = time.monotonic()
            if self._cb and now - self._last >= 0.4:
                self._last = now
                self._cb(self._sent, self._total)
        return block

    def close(self) -> None:
        self._fh.close()


class WebDAVUploader(Uploader):
    name = "webdav"
    display_name = "OpenList / WebDAV（推荐）"

    def __init__(self, cfg, secrets, *, chunk_size: int = 1024 * 1024) -> None:
        super().__init__(cfg, secrets)
        self.chunk_size = chunk_size
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "qgb/0.1 (webdav)"})

    # -------------------------------------------------- 基础

    @property
    def base_url(self) -> str:
        return (self.cfg.webdav_url or "").strip().rstrip("/")

    def _auth(self) -> HTTPBasicAuth | None:
        user = self.secrets.get(KEY_NETDISK_WEBDAV_USERNAME)
        pwd = self.secrets.get(KEY_NETDISK_WEBDAV_PASSWORD)
        if user and pwd:
            return HTTPBasicAuth(user, pwd)
        # OpenList 允许匿名访问时也走得通
        return None

    def _url(self, remote_path: str) -> str:
        """把远端路径拼成完整 URL（正确百分号编码，兼容中文文件名）。"""
        path = "/" + (remote_path or "").lstrip("/")
        return f"{self.base_url}{quote(path, safe='/')}"

    # -------------------------------------------------- 自检

    def test(self) -> tuple[bool, str]:
        if not self.base_url:
            return False, "未填写 WebDAV 地址"
        try:
            resp = self._session.request(
                "PROPFIND",
                self.base_url + "/",
                auth=self._auth(),
                headers={"Depth": "0"},
                timeout=(8, 20),
            )
        except requests.RequestException as exc:
            return False, f"无法连接：{exc.__class__.__name__}"

        if resp.status_code in (200, 207):
            return True, "连接正常，授权有效"
        if resp.status_code == 401:
            return False, "认证失败：用户名或密码不正确"
        if resp.status_code == 403:
            return False, "服务器拒绝访问（403）：请检查 OpenList 目录权限"
        if resp.status_code == 404:
            return False, "WebDAV 路径不存在（404）：请确认地址是否为 …/dav"
        return False, f"异常状态码 {resp.status_code}"

    # -------------------------------------------------- 目录

    def _exists(self, remote_dir: str, *, timeout: tuple[float, float] = (8.0, 20.0)) -> bool:
        """探测远端目录是否已存在（PROPFIND Depth:0）。"""
        try:
            resp = self._session.request(
                "PROPFIND",
                self._url(remote_dir),
                auth=self._auth(),
                headers={"Depth": "0"},
                timeout=timeout,
            )
        except requests.RequestException:
            return False
        return resp.status_code in (200, 207)

    def ensure_dir(self, remote_dir: str) -> None:
        """确保远端目录存在（逐级创建，**已存在的直接跳过**）。

        ⚠️ 必须容忍"已存在"，否则会踩到两个真实坑：

        1. **挂载点不能被 MKCOL**。OpenList 的 WebDAV 根下暴露的是各个存储的
           挂载点（例如 ``/baidu``）。对挂载点发 MKCOL 会被拒（实测返回 **403**），
           但那不是错误 —— 它本来就在那儿。
        2. 目录本来就可能存在（上一次搬运已建好）。WebDAV 规范里这种情况返回
           405，但各家实现不一致（OpenList 给的是 403）。

        所以策略是：**先 PROPFIND 判断存在性，存在就跳过**；真发 MKCOL 时，
        对 403 再用 PROPFIND 复核一次 —— 确认存在就视为成功，
        确认不存在才报错（真正的权限问题不会被吞掉）。
        """
        remote_dir = "/" + (remote_dir or "").strip("/")
        if remote_dir == "/":
            return

        # 常见路径：目标目录已存在（含首段是挂载点的情况）→ 一次探测就返回
        if self._exists(remote_dir):
            return

        segments = [s for s in remote_dir.split("/") if s]
        current = ""
        for seg in segments:
            current = f"{current}/{seg}"

            if self._exists(current):
                continue                      # 已存在（挂载点/上次建好的）→ 跳过

            url = self._url(current)
            try:
                resp = self._session.request(
                    "MKCOL", url, auth=self._auth(), timeout=(8, 30)
                )
            except requests.RequestException as exc:
                raise UploadError(
                    f"创建远端目录失败：{exc}",
                    hint="请确认 OpenList 正在运行，且 WebDAV 地址可访问。",
                ) from exc

            if resp.status_code in (201, 204, *_MKCOL_OK_ALREADY):
                continue
            if resp.status_code == 401:
                raise UploadError(
                    "创建远端目录被拒绝：认证失败",
                    hint="请在「网盘与凭据」页重新填写 OpenList 用户名与密码。",
                )
            if resp.status_code == 409:
                # 父目录缺失，理论上不会发生（我们是逐级创建的）
                continue

            # 403 最常见的原因就是"它其实已经存在"（挂载点、或并发创建），
            # 复核一次，避免把正常情况报成错误。
            if resp.status_code == 403 and self._exists(current):
                continue

            raise UploadError(
                f"创建远端目录失败：{current} 返回 {resp.status_code}",
                hint=(
                    "403 有两个已知原因，按顺序排查：\n"
                    "1. **OpenList 用户缺少「WebDAV 管理」权限**（最常见）。\n"
                    "   OpenList 的 WebDAV 对 PUT/MKCOL 单独校验权限位，"
                    "缺少时网页端和 API 都能正常读写、只有 WebDAV 报 403，"
                    "极易误判。请到 OpenList「用户」里编辑该账号，勾上"
                    "「WebDAV 管理」权限。\n"
                    "2. **远端根目录的第一段必须是 OpenList 的存储挂载点**。\n"
                    "   例如存储挂在 /baidu，远端根目录就应写成 /baidu/QQ群备份，"
                    "而不是 /QQ群备份。"
                ),
            )

    # -------------------------------------------------- 上传

    def upload(
        self,
        local_path: Path,
        remote_path: str,
        *,
        on_progress: ProgressCb | None = None,
    ) -> UploadResult:
        local_path = Path(local_path)
        if not local_path.is_file():
            raise UploadError(f"本地文件不存在：{local_path.name}")

        remote_dir, filename = self._split_dir(remote_path)
        self.ensure_dir(remote_dir)

        total = local_path.stat().st_size
        url = self._url(remote_path)

        log.info("正在上传 %s（%s）", filename, human_size(total))
        try:
            with local_path.open("rb") as fh:
                reader = _ProgressReader(fh, total, on_progress)
                headers = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(total),
                }
                resp = self._session.put(
                    url,
                    data=reader,
                    headers=headers,
                    auth=self._auth(),
                    timeout=(15, 1800),
                )
        except requests.RequestException as exc:
            raise UploadError(
                f"上传中断：{exc}",
                hint="请检查网络与 OpenList 状态；下次运行会自动重试。",
            ) from exc

        if resp.status_code not in (200, 201, 204):
            if resp.status_code == 401:
                raise UploadError(
                    "上传被拒绝：认证失败",
                    hint="请在「网盘授权」里重新填写 OpenList 用户名与密码。",
                )
            if resp.status_code == 507:
                raise UploadError(
                    "网盘空间不足（507）",
                    hint="请清理网盘空间后重试。",
                )
            raise UploadError(f"上传失败：服务器返回 {resp.status_code}")

        if on_progress:
            on_progress(total, total)

        result = UploadResult(remote_path=remote_path, size=total, verified=False)

        if self.cfg.verify_after_upload:
            actual = self.remote_size(remote_path)
            if actual is None:
                log.debug("无法回读校验大小（服务器未返回 Content-Length），跳过")
            elif actual != total:
                raise UploadError(
                    f"上传校验失败：远端 {human_size(actual)} ≠ 本地 {human_size(total)}",
                    hint="文件可能只上传了一部分，已保留本地副本，下次会重试。",
                )
            else:
                result.verified = True

        log.info("已上传：%s（%s）", filename, human_size(total))
        return result

    # -------------------------------------------------- 回读

    def remote_size(self, remote_path: str) -> int | None:
        url = self._url(remote_path)
        try:
            resp = self._session.head(url, auth=self._auth(), timeout=(8, 20))
        except requests.RequestException:
            return None
        if resp.status_code != 200:
            return None
        length = resp.headers.get("Content-Length")
        return int(length) if length and length.isdigit() else None
