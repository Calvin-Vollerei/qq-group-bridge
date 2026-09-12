"""本地目录适配器。

两个用途：
  1. **离线测试** —— 不需要任何真实账号即可验证整条流水线
  2. **真实部署** —— 若朋友的电脑上跑着 OneDrive / 坚果云 / Syncthing 等
     同步客户端，直接投递到其同步目录即可，等价于上传到网盘
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from ..errors import UploadError
from ..utils import human_size
from .base import ProgressCb, UploadResult, Uploader

__all__ = ["LocalUploader"]

log = logging.getLogger(__name__)


class LocalUploader(Uploader):
    name = "local"
    display_name = "本地目录 / 同步盘文件夹"

    @property
    def root(self) -> Path:
        return Path(self.cfg.local_root or "").expanduser()

    # -------------------------------------------------- 自检

    def build_remote_path(
        self, *, group_id: str, filename: str, folder: str = ""
    ) -> str:
        """本地适配器的路径**不拼 ``remote_root``**。

        为什么必须覆盖：``local_root`` 本身就是根目录，而基类会把
        ``remote_root``（默认 ``/QQ群备份``）也当作第一段路径加进去，
        结果是多出一层毫无意义的嵌套：

            期望：``D:\\网盘同步文件夹\\<群目录>\\文件.pdf``
            实际：``D:\\网盘同步文件夹\\QQ群备份\\<群目录>\\文件.pdf``

        对 WebDAV 而言 ``remote_root`` 是必要的（同一个 WebDAV 下有别的
        目录），但对本地目录它就是纯粹的重复。
        """
        segment = ""
        if self.cfg.split_by_group:
            segment = str(folder or group_id or "").strip()
        parts = [str(p).strip("/") for p in (segment, filename) if p]
        parts = [p for p in parts if p]
        return "/" + "/".join(parts)

    def test(self) -> tuple[bool, str]:
        if not self.cfg.local_root:
            return False, "未设置本地目标目录"
        root = self.root
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return False, f"无法创建目录：{exc}"
        if not os.access(root, os.W_OK):
            return False, "目录不可写"
        return True, f"目录可写：{root}"

    # -------------------------------------------------- 实现

    def ensure_dir(self, remote_dir: str) -> None:
        target = self._resolve(remote_dir)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UploadError(f"创建目录失败：{exc}") from exc

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

        dest = self._resolve(remote_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        total = local_path.stat().st_size
        tmp = dest.with_name(dest.name + ".part")
        try:
            shutil.copyfile(local_path, tmp)
            os.replace(tmp, dest)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise UploadError(
                f"写入目标目录失败：{exc}",
                hint="请检查目标目录权限与磁盘空间。",
            ) from exc

        if on_progress:
            on_progress(total, total)
        log.info("已投递：%s（%s）", dest.name, human_size(total))
        # 返回**远端路径**（服务端视角），与 uploader 接口里的其它实现一致。
        # 曾返回 dest（绝对本地路径），结果 remote_size() 再把它当远端路径解析时
        # 会二次拼接 local_root，Windows 上永远查不到文件（Linux 上因为
        # "/tmp/..." 加前缀后恰好仍是有效路径而侥幸通过）。
        return UploadResult(remote_path=remote_path, size=total, verified=True)

    def remote_size(self, remote_path: str) -> int | None:
        target = self._resolve(remote_path)
        try:
            return target.stat().st_size
        except OSError:
            return None

    # -------------------------------------------------- 内部

    def _resolve(self, remote_path: str) -> Path:
        """把远端路径映射到本地路径，并**阻断路径穿越**。

        同时接受两种写法：
          * 远端路径 ``/群目录/文件.pdf`` —— 正常调用方传的
          * **已在本根目录内**的绝对路径 —— ``upload()`` 历史上返回过这种值，
            幂等/重试路径会把它再传进来；不特殊处理就会二次拼接 local_root。
        指向根目录之外的绝对路径一律拒绝。
        """
        root = self.root.resolve()
        raw = str(remote_path or "")

        if raw:
            candidate = Path(raw)
            if candidate.is_absolute() or candidate.drive:
                try:
                    resolved = candidate.resolve()
                except OSError:
                    resolved = candidate
                if resolved == root or root in resolved.parents:
                    return candidate
                raise UploadError(
                    f"拒绝越界路径：{candidate.name}",
                    hint="目标路径不在本地根目录内，已被安全拦截。",
                )

        rel = "/" + raw.lstrip("/")
        parts = [p for p in rel.split("/") if p and p not in (".", "..")]
        candidate = root.joinpath(*parts)
        resolved = candidate.resolve()
        if root != resolved and root not in resolved.parents:
            raise UploadError(
                f"拒绝越界路径：{relative_display(parts)}",
                hint="文件名可能包含路径穿越字符，已被安全拦截。",
            )
        return candidate

    def describe_target(self) -> str:
        return str(self.root) if self.cfg.local_root else "(未设置)"


def relative_display(parts: list[str]) -> str:
    from ..redact import redact_text

    return redact_text("/".join(parts))
