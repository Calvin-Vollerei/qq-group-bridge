"""领域模型。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

__all__ = ["GroupFile", "TransferState", "TransferTask", "PollResult"]


class TransferState(str, enum.Enum):
    """单个文件的搬运状态机。"""

    DISCOVERED = "discovered"      # 已发现，等待处理
    FILTERED_OUT = "filtered_out"  # 被规则排除
    SKIPPED = "skipped"            # 已上传过，去重跳过
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"          # 已上传，等待本地清理
    DONE = "done"                  # 全流程结束
    FAILED = "failed"
    EXPIRED = "expired"            # 群文件已过期 / 已删除

    @property
    def is_terminal_ok(self) -> bool:
        return self in (
            TransferState.DONE,
            TransferState.UPLOADED,
            TransferState.SKIPPED,
            TransferState.FILTERED_OUT,
        )


@dataclass(slots=True)
class GroupFile:
    """来自 OneBot ``get_group_file_list`` 的单个文件。"""

    group_id: str
    file_id: str
    name: str
    size: int = 0
    busid: int = 102
    upload_time: int = 0
    uploader: str = ""
    uploader_name: str = ""
    download_times: int = 0
    #: 该文件所在的群文件夹 id（``/`` 表示根目录）
    folder_id: str = "/"

    @property
    def key(self) -> tuple[str, int, str]:
        """去重主键。注意 file_id 在不同 busid 下可能重复。"""
        return (self.group_id, int(self.busid), str(self.file_id))

    @property
    def size_mb(self) -> float:
        return self.size / 1024 / 1024

    def to_row(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "busid": int(self.busid),
            "file_id": str(self.file_id),
            "name": self.name,
            "size": int(self.size),
            "upload_time": int(self.upload_time),
            "uploader": self.uploader,
            "uploader_name": self.uploader_name,
            "download_times": int(self.download_times),
            "folder_id": self.folder_id,
        }

    @classmethod
    def from_api(cls, raw: dict[str, Any], *, group_id: str, folder_id: str = "/") -> "GroupFile":
        """从 OneBot 返回的原始字典构造（字段名做了兼容处理）。"""

        def pick(*names: str, default: Any = "") -> Any:
            for n in names:
                if n in raw and raw[n] is not None:
                    return raw[n]
            return default

        return cls(
            group_id=str(pick("group_id", "group", default=group_id) or group_id),
            file_id=str(pick("file_id", "id", default="")),
            name=str(pick("file_name", "name", default="(未命名)")),
            size=int(pick("file_size", "size", default=0) or 0),
            busid=int(pick("busid", "bus_id", default=102) or 102),
            upload_time=int(pick("upload_time", "modify_time", "mtime", default=0) or 0),
            uploader=str(pick("uploader", default="")),
            uploader_name=str(pick("uploader_name", default="")),
            download_times=int(pick("download_times", default=0) or 0),
            folder_id=str(pick("folder_id", default=folder_id) or folder_id),
        )


@dataclass(slots=True)
class TransferTask:
    """一条待执行/已执行的搬运任务。"""

    file: GroupFile
    state: TransferState = TransferState.DISCOVERED
    attempts: int = 0
    local_path: str = ""
    remote_path: str = ""
    sha256: str = ""
    error: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def key(self) -> tuple[str, int, str]:
        return self.file.key

    def __str__(self) -> str:  # pragma: no cover - 仅用于日志
        return f"{self.file.name}({self.file.size_mb:.1f}MB)"


@dataclass(slots=True)
class PollResult:
    """一次群轮询的结果。"""

    group_id: str
    files: list[GroupFile] = field(default_factory=list)
    folders_scanned: int = 0
    errors: list[str] = field(default_factory=list)
    truncated: bool = False
