"""搬运流水线（核心编排）。

一次完整循环（:meth:`Pipeline.run_once`）::

    检查登录 → 逐群拉取文件列表 → 落台账 → 过滤 → 认领任务
             → 下载（断点/重试） → 计算 sha256 → 上传 → 校验 → 删除本地临时文件

设计约束：
  * **保守并发**：默认同时只下 1 个文件，降低 QQ 风控概率
  * **可中断可恢复**：进程被杀后，重新启动会把「进行中」的任务退回待处理
  * **诚实失败**：任何一步失败都记进状态库并产生事件，不静默吞掉
  * **可离线运行**：所有外部依赖都通过注入，测试时换成假实现
"""

from __future__ import annotations

import logging
import random
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .downloader import Downloader, DownloadProgress
from .errors import DownloadError, NapCatError, QgbError, UploadError
from .filters import CompiledFilters, compile_filters
from .models import GroupFile, TransferState
from .naming import resolve_group_folder
from .secrets import SecretStore
from .store import StateStore
from .uploaders.base import Uploader
from .utils import free_disk_bytes, human_size, safe_filename, unique_path

__all__ = ["Pipeline", "PipelineEvent", "PipelineStats"]

log = logging.getLogger(__name__)

EventCb = Callable[["PipelineEvent"], None]


@dataclass(slots=True)
class PipelineEvent:
    """流水线对外事件（GUI 日志区 / 状态栏消费）。"""

    kind: str  # log | state | progress | error | stats | login
    level: str = "info"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class PipelineStats:
    discovered: int = 0
    filtered: int = 0
    skipped: int = 0
    downloaded: int = 0
    uploaded: int = 0
    failed: int = 0
    bytes_uploaded: int = 0
    started_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "discovered": self.discovered,
            "filtered": self.filtered,
            "skipped": self.skipped,
            "downloaded": self.downloaded,
            "uploaded": self.uploaded,
            "failed": self.failed,
            "bytes_uploaded": self.bytes_uploaded,
            "bytes_text": human_size(self.bytes_uploaded),
            "uptime": time.time() - self.started_at if self.started_at else 0.0,
        }


class Pipeline:
    """监控 → 下载 → 上传 的编排器。"""

    def __init__(
        self,
        cfg: AppConfig,
        store: StateStore,
        secrets: SecretStore,
        client,
        uploader: Uploader,
        *,
        on_event: EventCb | None = None,
        downloader: Downloader | None = None,
    ) -> None:
        self.cfg = cfg
        self.store = store
        self.secrets = secrets
        self.client = client
        self.uploader = uploader
        self.on_event = on_event
        self.filters: CompiledFilters = compile_filters(cfg.filters)
        self.downloader = downloader or Downloader(
            max_retries=cfg.monitor.max_retries,
            backoff_base=cfg.monitor.retry_backoff_sec,
        )
        self.tmp_dir = cfg.resolved_temp_dir()
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.stats = PipelineStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self._lock = threading.RLock()
        #: 群号 → 群名。取自 OneBot 的 get_group_list，用于「用群名做目录名」
        #: 这类型号；取不到就退回群号，绝不因此中断搬运。
        self._group_names: dict[str, str] = {}

    # -------------------------------------------------- 目录命名

    def folder_for(self, group_id: str) -> str:
        """算出某个群在网盘里的子目录名。

        优先级：**用户自定义 > 命名风格（群号/群名/群号_群名）**。
        群名缺失时一律退回群号 —— 目录名难看可以接受，搬运失败不行。
        """
        gid = str(group_id or "")
        if not gid:
            return ""
        if not self.cfg.upload.split_by_group:
            return ""
        return resolve_group_folder(
            gid,
            custom=(self.cfg.group_folder_names or {}).get(gid, ""),
            style=self.cfg.upload.folder_style,
            group_name=self._group_names.get(gid, ""),
        )

    def refresh_group_names(self) -> dict[str, str]:
        """从 OneBot 拉一次群名并缓存（失败时静默保留旧值）。"""
        try:
            groups = self.client.get_group_list()
        except Exception as exc:
            log.debug("拉取群名失败（将退回群号命名）：%s", type(exc).__name__)
            return self._group_names

        names: dict[str, str] = {}
        for item in groups or []:
            if isinstance(item, dict):
                gid = str(item.get("group_id") or "")
                if gid:
                    names[gid] = str(item.get("group_name") or "")
        if names:
            self._group_names.update(names)
        return self._group_names

    def group_names(self) -> dict[str, str]:
        return dict(self._group_names)

    # -------------------------------------------------- 事件

    def _emit(self, kind: str, message: str = "", level: str = "info", **data: Any) -> None:
        event = PipelineEvent(kind=kind, level=level, message=message, data=data)
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:  # 回调异常不能拖垮流水线
                log.debug("事件回调抛出异常，已忽略", exc_info=True)
        if message:
            getattr(log, "warning" if level == "warning" else "info", log.info)(
                "%s", message
            )
        if kind in ("error", "file"):
            try:
                self.store.add_event(level, message, group_id=str(data.get("group_id", "")),
                                     name=str(data.get("name", "")))
            except Exception:
                pass

    # -------------------------------------------------- 生命周期

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._pause.is_set()

    def start(self) -> bool:
        """启动后台监控线程。返回是否真正启动（已在跑则 False）。"""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._pause.clear()
            self.stats = PipelineStats(started_at=time.time())
            self._recover_interrupted()
            self._thread = threading.Thread(target=self._loop, name="qgb-pipeline", daemon=True)
            self._thread.start()
        self._emit("state", "监控已启动", state="running")
        return True

    def stop(self, *, timeout: float = 20.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self._emit("state", "监控已停止", state="stopped")

    def pause(self) -> None:
        self._pause.set()
        self._emit("state", "监控已暂停", state="paused")

    def resume(self) -> None:
        self._pause.clear()
        self._emit("state", "监控已恢复", state="running")

    def _recover_interrupted(self) -> None:
        """上次异常退出留下的「进行中」任务退回待处理。"""
        try:
            n = self.store.reset_stale(
                (
                    TransferState.DOWNLOADING,
                    TransferState.DOWNLOADED,
                    TransferState.UPLOADING,
                )
            )
        except QgbError as exc:
            log.warning("恢复中断任务失败：%s", exc)
            return
        if n:
            self._emit("log", f"已恢复 {n} 个上次中断的任务", level="warning")

    # -------------------------------------------------- 主循环

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._pause.is_set():
                    self._stop.wait(2.0)
                    continue
                self.run_once()
            except Exception as exc:  # 任何未预期异常都不能让线程死掉
                log.exception("流水线循环异常")
                self._emit("error", f"循环异常：{exc}", level="error")

            wait = self._next_interval()
            self._stop.wait(wait)

    def _next_interval(self) -> float:
        base = max(30, int(self.cfg.monitor.poll_interval_sec))
        jitter = max(0, int(self.cfg.monitor.jitter_sec))
        return base + (random.uniform(0, jitter) if jitter else 0.0)

    # -------------------------------------------------- 单次循环

    def _counters(self) -> tuple[int, ...]:
        s = self.stats
        return (
            s.discovered, s.filtered, s.skipped,
            s.downloaded, s.uploaded, s.failed, s.bytes_uploaded,
        )

    def _cycle_delta(self, before: tuple[int, ...]) -> PipelineStats:
        """把累计计数换算成「本轮增量」。

        对外语义更清晰：``run_once()`` 返回的是**这一轮**干了什么，
        而 ``self.stats`` 始终是自启动以来的**累计值**（GUI 状态栏用）。
        """
        now = self._counters()
        d = [a - b for a, b in zip(now, before)]
        return PipelineStats(
            discovered=d[0], filtered=d[1], skipped=d[2],
            downloaded=d[3], uploaded=d[4], failed=d[5], bytes_uploaded=d[6],
            started_at=self.stats.started_at,
        )

    def run_once(self) -> PipelineStats:
        """执行一轮「拉取 + 搬运」。返回**本轮增量**统计。"""
        if not self.stats.started_at:
            self.stats.started_at = time.time()

        before = self._counters()

        # 暂停状态（人工暂停，或上次检查发现 QQ 掉线）时不做任何网络动作
        if self._pause.is_set():
            self._emit("stats", **self.stats.as_dict())
            return self._cycle_delta(before)

        self._check_login()
        if self._pause.is_set():
            self._emit("stats", **self.stats.as_dict())
            return self._cycle_delta(before)

        # 只在「目录名需要群名」时才去拉群名，省钱又省一次接口调用
        if self.cfg.upload.folder_style in ("name", "id_name"):
            self.refresh_group_names()

        self._poll_all_groups()
        self._drain_pending()
        self._emit("stats", **self.stats.as_dict())
        return self._cycle_delta(before)

    # -------------------------------------------------- 登录检查

    def _check_login(self) -> None:
        info = self.client.get_login_info()
        self._emit("login", f"QQ 状态：{info.message}", state=info.category)
        if not info.online:
            self._emit(
                "error",
                "QQ 未登录或登录已失效，已暂停监控",
                level="warning",
                hint="请使用手机 QQ 扫描二维码重新登录。",
            )
            self._pause.set()

    # -------------------------------------------------- 轮询

    def _poll_all_groups(self) -> None:
        for group_id in self.cfg.groups:
            if self._stop.is_set():
                return
            try:
                self._poll_group(group_id)
            except NapCatError as exc:
                self._emit("error", f"群 {group_id} 拉取失败：{exc}", level="warning", group_id=group_id)
            except Exception as exc:
                self._emit("error", f"群 {group_id} 拉取异常：{exc}", level="error", group_id=group_id)

    def _poll_group(self, group_id: str) -> None:
        files = self.client.fetch_group_files(
            group_id,
            recursive=self.cfg.monitor.recursive_folders,
            page_size=self.cfg.monitor.page_size,
        )

        new_count = 0
        for gf in files:
            if self._stop.is_set():
                return
            self.store.upsert_file(gf)

            decision = self.filters.check(gf.name, gf.size)
            if not decision.accepted:
                if self.store.claim(gf):
                    self.store.mark(
                        gf.key, TransferState.FILTERED_OUT, error=decision.reason
                    )
                    self.stats.filtered += 1
                continue

            if self.store.is_settled(gf.key):
                continue

            if self.store.claim(gf):
                new_count += 1
                self.stats.discovered += 1
                self._emit(
                    "file",
                    f"发现新文件：{gf.name}（{human_size(gf.size)}）",
                    group_id=group_id,
                    name=gf.name,
                    size=gf.size,
                )

        self._emit(
            "log",
            f"群 {group_id}：本轮扫描 {len(files)} 个文件，新增 {new_count} 个待搬运",
            group_id=group_id,
        )

    # -------------------------------------------------- 搬运

    def _drain_pending(self) -> None:
        limit = max(1, int(self.cfg.monitor.download_concurrency)) * 50
        pending = self.store.pending(limit=limit)

        for row in pending:
            if self._stop.is_set():
                return
            if self._pause.is_set():
                return
            try:
                self._process_row(row)
            except QgbError as exc:
                self._handle_failure(row, exc)
            except Exception as exc:
                self._handle_failure(row, QgbError(str(exc)))

    def _handle_failure(self, row: dict[str, Any], exc: QgbError) -> None:
        key = (row["group_id"], int(row["busid"]), str(row["file_id"]))
        attempts = int(row.get("attempts") or 0) + 1
        message = getattr(exc, "message", str(exc))
        self.store.mark(key, TransferState.DISCOVERED, error=message, bump_attempts=True)

        if attempts >= max(1, int(self.cfg.monitor.max_retries)):
            self.store.mark(key, TransferState.FAILED, error=message)
            self.stats.failed += 1
            self._emit(
                "error",
                f"失败（已重试 {attempts} 次）：{row['name']} —— {message}",
                level="error",
                group_id=row["group_id"],
                name=row["name"],
                hint=getattr(exc, "hint", ""),
            )
        else:
            self._emit(
                "log",
                f"第 {attempts} 次尝试失败，稍后重试：{row['name']} —— {message}",
                level="warning",
                group_id=row["group_id"],
                name=row["name"],
            )

    def _process_row(self, row: dict[str, Any]) -> None:
        gf = GroupFile(
            group_id=row["group_id"],
            file_id=str(row["file_id"]),
            name=row["name"],
            size=int(row["size"] or 0),
            busid=int(row["busid"]),
        )
        key = gf.key

        # 0) 磁盘空间闸门
        min_free = int(self.cfg.monitor.min_free_disk_gb * 1024 ** 3)
        if min_free and free_disk_bytes(self.tmp_dir) < min_free + gf.size:
            raise DownloadError(
                f"可用磁盘不足（低于 {self.cfg.monitor.min_free_disk_gb:.1f}GB 阈值）",
                hint="请清理磁盘空间，或调低该阈值。",
            )

        # 1) 体积上限
        max_mb = self.cfg.monitor.max_file_mb
        if max_mb and gf.size_mb > max_mb:
            self.store.mark(key, TransferState.FILTERED_OUT, error=f"超过 {max_mb}MB 上限")
            self.stats.filtered += 1
            return

        local_dir = self.tmp_dir / f"g{gf.group_id}_{gf.busid}_{gf.file_id}"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / safe_filename(gf.name)

        # 2) 下载
        if not local_path.exists() or (gf.size and local_path.stat().st_size != gf.size):
            self.store.mark(key, TransferState.DOWNLOADING, local_path=str(local_path))
            self._emit("log", f"开始下载：{gf.name}（{human_size(gf.size)}）",
                       group_id=gf.group_id, name=gf.name)

            url = self.client.get_group_file_url(gf.group_id, gf.file_id, gf.busid)

            def on_progress(p: DownloadProgress) -> None:
                self._emit(
                    "progress",
                    data={
                        "name": p.name,
                        "percent": round(p.percent, 1),
                        "downloaded": p.downloaded,
                        "total": p.total,
                        "speed": p.speed_bps,
                        "eta": p.eta_sec,
                    },
                )

            try:
                result = self.downloader.download(
                    url,
                    local_path,
                    expected_size=gf.size or None,
                    on_progress=on_progress,
                )
            except DownloadError as exc:
                # 直链过期：清掉本地残留，让下轮重新取链
                if "直链已过期" in str(exc):
                    local_path.unlink(missing_ok=True)
                raise

            self.store.mark(
                key, TransferState.DOWNLOADED, local_path=str(local_path), sha256=result.sha256
            )
            self.stats.downloaded += 1
            digest = result.sha256
        else:
            from .utils import sha256_file

            digest = sha256_file(local_path)
            self.store.mark(key, TransferState.DOWNLOADED, local_path=str(local_path), sha256=digest)

        # 3) 上传
        remote_path = self.uploader.build_remote_path(
            group_id=gf.group_id, filename=safe_filename(gf.name)
        )
        self.store.mark(key, TransferState.UPLOADING, remote_path=remote_path)
        self._emit("log", f"正在上传：{gf.name}", group_id=gf.group_id, name=gf.name)

        size = local_path.stat().st_size
        self.uploader.upload(
            local_path,
            remote_path,
            on_progress=lambda sent, total: self._emit(
                "progress",
                data={
                    "name": gf.name,
                    "percent": round(sent / total * 100, 1) if total else 0.0,
                    "phase": "upload",
                },
            ),
        )

        self.store.mark(key, TransferState.UPLOADED, remote_path=remote_path)
        self.stats.uploaded += 1
        self.stats.bytes_uploaded += size
        self._emit("file", f"已上传：{gf.name}", level="info",
                   group_id=gf.group_id, name=gf.name, remote_path=remote_path)

        # 4) 本地清理
        self._cleanup_local(key, local_dir)

    # -------------------------------------------------- 清理

    def _cleanup_local(self, key: tuple[str, int, str], local_dir: Path) -> None:
        keep_days = int(self.cfg.monitor.keep_local_days or 0)
        if keep_days > 0:
            self.store.mark(key, TransferState.DONE)
            self._emit(
                "log",
                f"本地副本保留 {keep_days} 天：{local_dir.name}",
            )
            return

        try:
            shutil.rmtree(local_dir, ignore_errors=True)
        except OSError as exc:
            log.warning("清理临时文件失败：%s", exc.__class__.__name__)
        self.store.mark(key, TransferState.DONE)

    def purge_old_locals(self) -> int:
        """清理超过保留期的本地副本（保留期 > 0 时才需要）。"""
        keep_days = int(self.cfg.monitor.keep_local_days or 0)
        if keep_days <= 0 or not self.tmp_dir.is_dir():
            return 0

        cutoff = time.time() - keep_days * 86400
        removed = 0
        for child in self.tmp_dir.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        if removed:
            self._emit("log", f"已清理 {removed} 个过期的本地副本")
        return removed
