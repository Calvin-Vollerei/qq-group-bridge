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
from .errors import TransientError  # noqa: E402
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
    #: **本轮**扫描新登记的文件数（每轮开始时清零）。
    #:
    #: 为什么要单独一个字段：界面上的「已见」「待处理」是**累计**值
    #: （库里所有记录的计数），用户会误以为那是'一次探测到的数量'。
    #: 这个字段才是'刚才这一轮扫到了几个新文件'。
    new_last_cycle: int = 0
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
            "new_last_cycle": self.new_last_cycle,
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
            stall_timeout=getattr(cfg.monitor, "stall_timeout_sec", 20.0),
        )
        self.tmp_dir = cfg.resolved_temp_dir()
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.stats = PipelineStats()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pause = threading.Event()
        self._lock = threading.RLock()
        #: 一轮「拉取+搬运」正在进行。用于避免**扫描叠加**：
        #: 上一轮还没跑完就又点「立即刷新」，会让同一个群被重复枚举、
        #: 同一批文件被重复登记（用户实测反馈过重复搬运）。
        self._busy = threading.Event()
        #: 每群「(文件名, 大小) -> 当前 file_id」的缓存，**每轮清空**。
        #: 为什么需要：transfers 里的 file_id 是登记时固定的，而群文件的 file_id
        #: 会随会话变化（实测：待处理项按 file_id 命中率 0%，按文件名命中 50-100%）。
        #: 用旧 id 取直链会失败（NapCat 报 fileUUID not found），所以下载前
        #: 用名字+大小从**当前**群列表里解析出有效 id。
        self._id_cache: dict[str, tuple[dict[tuple[str, int], str], bool]] = {}
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

    @property
    def busy(self) -> bool:
        """是否正在执行一轮（界面据此避免重复触发扫描）。"""
        return self._busy.is_set()

    def run_once(self) -> PipelineStats:
        """执行一轮「拉取 + 搬运」。返回**本轮增量**统计。

        **不重入**：一轮未结束时再次调用会直接返回增量（0），避免同一批文件
        被重复枚举/重复登记。
        """
        if self._busy.is_set():
            log.debug("上一轮尚未结束，跳过本次 run_once")
            return PipelineStats()
        self._busy.set()
        try:
            return self._run_once_locked()
        finally:
            self._busy.clear()

    def _run_once_locked(self) -> PipelineStats:
        if not self.stats.started_at:
            self.stats.started_at = time.time()
        # 本轮新发现计数：每轮清零，供界面显示'本次扫描发现几个新文件'
        self.stats.new_last_cycle = 0
        self._id_cache.clear()          # 群文件 id 每轮重新解析

        # 顺手清理陈旧临时目录：keep_local_days=0 只在**上传成功**后删副本，
        # 下载中断/上传一直失败的文件会永久留着（实测积了 129 个副本、14GB）。
        # 只清 3 天以上的，不会碰到正在处理的文件。
        try:
            self.purge_orphan_temps(older_than_days=3.0)
        except Exception:  # noqa: BLE001 - 清理失败不该影响搬运
            log.debug("清理陈旧临时目录失败", exc_info=True)

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
            # 永久排除：直接跳过，不登记、不下载、不重试（省流量）。
            # 必须在这里显式查名单 —— is_settled() 依赖 transfers 里已有行，
            # 而 mark() 不建行，所以从没记录过的被排除文件光靠 SKIPPED 拦不住。
            if self.store.is_excluded(gf.key):
                continue
            self.store.upsert_file(gf)

            decision = self.filters.check(gf.name, gf.size)
            if not decision.accepted:
                # ⚠️ 这里必须用 claim() 判定：它对**已完结**的记录返回 False，
                # 所以被过滤的文件第一轮会被标记为 FILTERED_OUT 并计数一次，
                # 后续轮次不再重复计数。早先我图省事改成直接 mark，就把这个
                # 语义弄丢了（test_settled_filtered_file_not_recounted 立刻挂掉）。
                if self.store.claim(gf):
                    self.store.mark(
                        gf.key, TransferState.FILTERED_OUT, error=decision.reason
                    )
                    self.stats.filtered += 1
                continue

            if self.store.is_settled(gf.key):
                continue

            # 再按 (群, 文件名, 大小) 查一遍：file_id 会随会话变化，但这三项不会
            # —— 防止组件重启后把已搬过的文件重新下载上传一遍。
            # 只认真正搬成功的（DONE/UPLOADED），见 store.is_uploaded_like 的说明。
            if self.store.is_uploaded_like(gf.group_id, gf.name, gf.size):
                continue

            if self.store.claim(gf):
                new_count += 1
                self.stats.discovered += 1
                self.stats.new_last_cycle += 1
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
        # 每轮处理多少个待处理项。
        # ⚠️ 早先是 concurrency*50（=50）：待处理动辄几万条，用户"置顶/优先执行"
        # 后仍要等很久才轮到，看起来像没生效。实测本机 45,245 条待处理时
        # 全量排序仅 156 ms，所以这里放大到几百个也不影响；失败的文件在同一批里
        # 只处理一次（见下面的 attempted），不会因此放大重试。
        limit = max(200, int(self.cfg.monitor.download_concurrency) * 200)
        # 走**手工排序**的队列：置顶 → 手工顺序 → 未排序按发现时间。
        # 用户可在「监控」页的搬运记录里置顶 / 上移下移来改变下载顺序。
        pending = self.store.pending_ordered(limit=limit)

        # 置顶/优先的项**独占第一批**。
        #
        # 用户实测反馈"优先下载、置顶还是不起效果"。原因不是排序没生效，而是
        # 每轮会处理 limit 个（默认 200），置顶项虽然排在第一位，却和 199 个普通
        # 项混在**同一批**里跑；用户盯着日志看，前面几十条都是别的文件，
        # 主观感受就是"没效果"。
        # 现在先只跑置顶/优先的那批（通常只有几个到几十个），跑完再继续其余。
        # 下一轮（或本轮同一 _drain_pending 内）接着处理普通项，进度不受影响。
        pinned = [r for r in pending if r.get("pinned")]
        if pinned:
            pending = pinned
        else:
            pending = pending

        #: 本次筛选内已经处理过的文件（含失败的）。
        #:
        #: ⚠️ 为什么必须有这个跳过逻辑：``_handle_failure`` 会把文件 mark 回
        #: ``DISCOVERED``（并 attempts+1），而本循环遍历的是**开始时取的快照**。
        #: 于是队首那个失败的文件会被**立刻重新处理**：一个坏文件连续占满
        #: 3 次重试，后面的文件一直排不上队 —— 用户看到的是"卡在一个文件上"。
        #: 正确行为：同一批内先跳到下一个文件，**下次循环**再回来重试它
        #: （重试退避由循环间隔自然实现）。
        attempted: set[tuple[str, int, str]] = set()

        for row in pending:
            if self._stop.is_set():
                return
            if self._pause.is_set():
                return

            key = (str(row["group_id"]), int(row["busid"]), str(row["file_id"]))
            if key in attempted:
                continue
            attempted.add(key)

            try:
                self._process_row(row)
            except QgbError as exc:
                self._handle_failure(row, exc)
            except Exception as exc:
                self._handle_failure(row, QgbError(str(exc)))

    def _handle_failure(self, row: dict[str, Any], exc: QgbError) -> None:
        key = (row["group_id"], int(row["busid"]), str(row["file_id"]))
        message = getattr(exc, "message", str(exc))

        # 临时性故障（例如 NapCat 的 fileUUID 未就绪）：退回待处理、
        # **不 +1 重试次数**，稍后自然重试。否则一次组件状态抖动就会把
        # 整批文件永久标成「失败」，用户看到的是"任务不推进"。
        if isinstance(exc, TransientError):
            # 设冷却：否则下一轮它又排队首，把尝试名额全吃掉（见 config 里的说明）
            cooldown = float(getattr(self.cfg.monitor, "transient_cooldown_sec", 300.0))
            self.store.mark(key, TransferState.DISCOVERED, error=message,
                            retry_after=cooldown)
            self._emit(
                "log",
                f"暂时跳过（稍后自动重试）：{row['name']} —— {message}",
                level="warning",
                group_id=row["group_id"],
                name=row["name"],
                hint=getattr(exc, "hint", ""),
            )
            return

        attempts = int(row.get("attempts") or 0) + 1
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

    def _live_file_id(self, gf: GroupFile) -> tuple[str, bool]:
        """解析该文件**当前**有效的 file_id。

        返回 ``(file_id, 列表可用)``：

        * ``file_id`` 非空 → 用这个 id 取直链；
        * ``file_id`` 为空且 ``列表可用`` 为真 → 群列表里确实没有它（已删除/改名）；
        * ``file_id`` 为空但 ``列表可用`` 为假 → **遍历失败**，不能断定文件不存在，
          此时应保守跳过（当作临时故障），避免误杀真实文件。

        ⚠️ "列表可用"这个标志是必要的：`walk_group_files` 可能因网络/接口异常
        半途返回，若只看到"没找到"就判定文件被删，会把仍在群里的文件永久跳过。
        """
        cached = self._id_cache.get(gf.group_id)
        if cached is None:
            table: dict[tuple[str, int], str] = {}
            ok = True
            try:
                for f in self.client.walk_group_files(
                    gf.group_id,
                    recursive=self.cfg.monitor.recursive_folders,
                    page_size=self.cfg.monitor.page_size,
                ):
                    table.setdefault((f.name, int(f.size or 0)), str(f.file_id))
                if not table:
                    ok = False          # 一个都没拿到：多半是接口异常，不能当"文件都没有"
            except Exception as exc:  # noqa: BLE001 - 解析失败不该中断整轮
                log.debug("解析群 %s 的文件列表失败：%s", gf.group_id, exc)
                ok = False
            cached = (table, ok)
            self._id_cache[gf.group_id] = cached
        table, ok = cached
        return table.get((gf.name, int(gf.size or 0)), ""), ok

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
        #
        # ⚠️ 跳过下载的前提是"本地已有一份**完整**副本"：``gf.size`` 已知时按大小
        #    校验；未知时保守认为"存在即可用"（否则会反复重下大文件）。
        #    实测踩过：上传连续失败时，582MB 的文件被重下了 129 次。
        already_local = local_path.exists() and (
            not gf.size or local_path.stat().st_size == gf.size
        )
        if already_local:
            self._emit("log", f"本地已有完整副本，跳过下载：{gf.name}",
                       group_id=gf.group_id, name=gf.name)
        if not already_local:
            self.store.mark(key, TransferState.DOWNLOADING, local_path=str(local_path))
            self._emit("log", f"开始下载：{gf.name}（{human_size(gf.size)}）",
                       group_id=gf.group_id, name=gf.name)

            # 取直链前先确认 file_id 是**当前**有效的：transfers 里存的是登记时的值，
            # 而群文件的 id 会变（实测：待处理项按 file_id 命中率 0%、按文件名 50-100%，
            # 用旧 id 取直链 100% 失败）。所以这里用名字+大小解析出当前 id。
            live_id, listing_ok = self._live_file_id(gf)
            if live_id and int(row.get("absent_count") or 0):
                self.store.mark(gf.key, TransferState.DISCOVERED, reset_absent=True)
            if live_id and live_id != gf.file_id:
                log.info("文件 %s 的 file_id 已变化，改用当前值", gf.name)
                self.store.set_file_id(gf.key, live_id)
                gf = GroupFile(group_id=gf.group_id, file_id=live_id, name=gf.name,
                               size=gf.size, busid=gf.busid)
                key = gf.key
                row = dict(row)
                row["file_id"] = live_id
            elif not live_id:
                if listing_ok:
                    # 列表完整且确实没有它。连续几次都确认不到就判定"已失效"，
                    # 不再无限重试 —— 否则被删除的历史记录会永远占着队列
                    #（用户明确要求"不要累加"）。
                    absent = int(row.get("absent_count") or 0) + 1
                    limit = int(getattr(self.cfg.monitor, "absent_limit", 3))
                    if absent >= limit:
                        self.store.mark(
                            gf.key, TransferState.EXPIRED,
                            error=f"群文件列表里已找不到该文件（连续 {absent} 次确认）",
                            bump_absent=True, reset_absent=True,
                        )
                        self.stats.skipped += 1
                        self._emit(
                            "log",
                            f"已失效（群内已无此文件，不再重试）：{gf.name}",
                            level="warning",
                            group_id=gf.group_id,
                            name=gf.name,
                        )
                        return
                    self.store.mark(gf.key, TransferState.DISCOVERED,
                                    bump_absent=True, retry_after=60.0)
                    raise TransientError(
                        f"群文件列表里已找不到「{gf.name}」"
                        f"（第 {absent}/{limit} 次确认，可能已被删除或改名）",
                        hint="稍后再确认一次；连续几次都找不到就会标记为「已过期」，"
                             "不再占用队列。",
                    )
                # 列表没取全：不能断定文件不存在，保守当作临时故障
                raise TransientError(
                    f"暂时无法核对「{gf.name}」是否仍在群里（群文件列表未取全）",
                    hint="稍后会自动重试；若持续出现，请重启一次 QQ 组件。",
                )

            try:
                url = self.client.get_group_file_url(gf.group_id, gf.file_id, gf.busid)
            except NapCatError as exc:
                # code=-134（sendGroupFileDownloadReq 失败）：
                # QQ 侧取不到下载直链 —— 常见于文件已被删除、权限被收回、
                # 或该文件对登录账号不可见。这类**重试也没用**，
                # 所以直接判永久失败并写清原因，不要像临时故障那样反复重试。
                msg = str(exc)
                if "-134" in msg or "文件下载失败" in msg:
                    self.store.mark(
                        key, TransferState.FAILED,
                        error=f"QQ 侧取不到下载直链（{msg[:60]}）")
                    self.stats.failed += 1
                    self._emit(
                        "log",
                        f"放弃（取不到下载直链，重试无意义）：{gf.name} —— "
                        "该文件可能已被删除或权限已变更",
                        level="warning",
                        group_id=gf.group_id, name=gf.name,
                    )
                    return
                raise

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
                # 直链过期：清掉**不完整的残片**，让下轮重新取链。
                #
                # ⚠️ 这里原本是 ``local_path.unlink()``，把**已下好的完整副本**也删了。
                #    后果很严重：一个 582MB 的文件在上传一直失败（405）时，
                #    每次重试都会从零重下一遍 —— 用户库里实测同一个文件留了
                #    129 个副本、占 14 GB。只清 .part，完整文件必须留着复用。
                if "直链已过期" in str(exc):
                    local_path.with_name(local_path.name + ".part").unlink(missing_ok=True)
                    if local_path.exists() and (not gf.size
                                                or local_path.stat().st_size != gf.size):
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

    def purge_orphan_temps(self, *, older_than_days: float = 3.0) -> int:
        """清理 tmp 里的**陈旧残留**，返回清掉的目录数。

        为什么需要：``keep_local_days=0`` 只在**上传成功**后删本地副本；
        下载到一半被中断、或上传一直失败的文件会永久留在 tmp 里。
        实测用户库积了 129 个副本、14 GB。

        只清**足够旧**的（默认 3 天），避免误删正在处理的文件。
        """
        if not self.tmp_dir.is_dir():
            return 0
        cutoff = time.time() - max(0.0, older_than_days) * 86400
        removed = 0
        for child in self.tmp_dir.iterdir():
            try:
                if not child.is_dir() or child.stat().st_mtime >= cutoff:
                    continue
                # 正在处理中的不删：transfers 里还有它的记录且状态是中间态
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
            except OSError:
                continue
        if removed:
            self._emit("log", f"已清理 {removed} 个陈旧临时目录（释放磁盘）")
        return removed

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
