"""应用门面层（**不依赖 tkinter**）。

GUI 只是这一层之上的视图。这样拆分的三个好处：
  1. 业务逻辑可以**无头测试**（本项目的测试就是这么跑的）
  2. 将来要加托盘 / 服务化 / 命令行模式，不用碰界面代码
  3. 界面线程只负责「读状态、发指令」，不会因为网络阻塞而卡死

线程模型：
  * 所有耗时操作（建流水线、启 NapCat、取二维码、测网盘）都在**工作线程**里跑
  * 结果统一投递到 :attr:`AppController.events` 队列
  * 界面线程用 ``after()`` 定时抽取队列 —— 这是 tkinter 唯一安全的跨线程方式
"""

from __future__ import annotations

import logging
import queue
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig, app_paths, default_config_path, load_config, save_config
from .errors import ConfigError, CredentialError, NapCatError, QgbError
from .logging_setup import setup_logging
from .models import TransferState
from .napcat.client import OneBotClient
from .napcat.process import NapCatManager
from .napcat.qr import QrCode, check_login, fetch_qrcode, webui_url
from .netdisk_client import detect_clients, open_client_or_web
from .openlist import OpenListManager
from .paths import app_base_dir, data_dir_source
from .paths import data_dir_source
from .pipeline import Pipeline
from .secrets import (
    KEY_NETDISK_WEBDAV_PASSWORD,
    KEY_NETDISK_WEBDAV_USERNAME,
    KEY_NAPCAT_WEBUI_TOKEN,
    KEY_QQ_ONEBOT_TOKEN,
    SecretStore,
    default_data_dir,
    default_store_path,
)
from .store import StateStore
from .uploaders import available_adapters, build_uploader

__all__ = ["AppController", "Event"]

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Event:
    """投递给界面的事件。"""

    kind: str          # log | state | stats | login | file | progress | error
                       # | qr | napcat | netdisk | creds | info | ready
    level: str = "info"
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class AppController:
    """把配置、凭据、状态库、NapCat、流水线组织成界面可直接调用的门面。"""

    def __init__(self, *, config_path: Path | None = None, log_dir: Path | None = None) -> None:
        self.data_dir = default_data_dir()
        self.paths = app_paths()
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.log_dir = Path(log_dir) if log_dir else Path(self.paths["logs"])

        self.events: "queue.Queue[Event]" = queue.Queue()
        self._lock = threading.RLock()

        # 延迟构造：凭据库在非 Windows 上可能失败，但不应导致整个程序起不来
        self.config: AppConfig = AppConfig()
        self.secrets: SecretStore | None = None
        self.store: StateStore | None = None
        self.napcat: NapCatManager | None = None
        self.pipeline: Pipeline | None = None

        self._qr: QrCode = QrCode()
        self._qr_seq = 0            # 二维码请求序号：新请求取代旧请求
        self._qr_seq_ok = 0
        self._qr_at = 0.0            # 最近一次成功取码的时间（monotonic）
        self._busy: set[str] = set()
        #: 群号 → 群名 缓存（界面「按群名一键生成目录名」用）
        self._group_names: dict[str, str] = {}

    # ================================================================ 启动

    def load(self) -> None:
        """加载配置与凭据；失败时以「降级可用」的方式继续（界面要能显示错误）。"""
        self.data_dir = default_data_dir()

        # 日志目录理论上一定可写（paths 已探针验证），但仍要兜底：
        # 日志失败不应导致整个程序起不来。
        try:
            setup_logging(self.log_dir, keep_days=self.config.log_keep_days)
        except OSError as exc:
            self.log_dir = Path(tempfile.gettempdir()) / "QQGroupBridge-logs"
            try:
                setup_logging(self.log_dir, keep_days=self.config.log_keep_days)
            except OSError:
                pass
            self._post(
                "error",
                f"日志目录不可写（{type(exc).__name__}），已回退到临时目录",
                level="warning",
            )

        self.config = load_config(self.config_path)

        try:
            self.secrets = SecretStore(default_store_path())
            self.secrets.health()  # 触发一次真实加载，尽早暴露损坏
        except QgbError as exc:
            self.secrets = None
            self._post("error", f"凭据库不可用：{exc.message}", hint=exc.hint)
        except Exception as exc:  # 极端情况也不许崩
            self.secrets = None
            self._post("error", f"凭据库异常：{type(exc).__name__}")

        try:
            self.store = StateStore(self.data_dir / "state.db")
        except QgbError as exc:
            self.store = None
            self._post("error", f"状态库不可用：{exc.message}", hint=exc.hint)
        except OSError as exc:
            self.store = None
            self._post("error", f"状态库路径不可写：{type(exc).__name__}", level="warning")

        self.napcat = NapCatManager(
            self.config.napcat, self.data_dir, log_dir=self.log_dir
        )

        # OpenList（WebDAV 中转）：独立进程，忘了开就只会显示一句"连接失败"，
        # 极难联想到根因。这里按需自动拉起（只在目标指向本机时）。
        self.openlist = OpenListManager(
            self.config.upload,
            app_base=app_base_dir(),
            data_dir=self.data_dir,
            log_dir=self.log_dir,
        )

        self._post("ready", "初始化完成")

    def ensure_openlist(self, *, silent: bool = False) -> bool:
        """按需启动本机 OpenList（程序启动时调用，也可手动触发）。"""
        if self.openlist is None or not self.config.upload.manage_openlist:
            return False

        def task():
            ok, message = self.openlist.ensure_running()
            level = "info" if ok else "warning"
            if not silent or not ok:
                self._post("openlist", message, level=level, ok=ok, **self.openlist.status().as_dict())
            return ok

        return self.run_async("openlist_ensure", task)

    def openlist_status(self) -> dict[str, Any]:
        """同步查询 OpenList 状态（只做端口探测，很快）。"""
        if self.openlist is None:
            return {}
        try:
            return self.openlist.status().as_dict()
        except Exception as exc:
            log.warning("查询 OpenList 状态失败：%s", type(exc).__name__)
            return {}

    def start_openlist(self) -> bool:
        def task():
            try:
                self.openlist.start()
            except QgbError as exc:
                self._post("openlist", exc.message, level="error", hint=exc.hint)
                return False
            self._post("openlist", "OpenList 已启动", **self.openlist.status().as_dict())
            return True

        return self.run_async("openlist_start", task)

    def stop_openlist(self) -> bool:
        def task():
            self.openlist.stop()
            self._post("openlist", "已停止由本程序启动的 OpenList",
                       **self.openlist.status().as_dict())
            return True

        return self.run_async("openlist_stop", task)

    def openlist_webui_url(self) -> str:
        """OpenList 管理后台地址（供「打开后台」按钮使用，不含凭据）。"""
        return self.openlist.webui_url() if self.openlist else ""

    # ================================================================ 事件

    def _post(self, kind: str, message: str = "", /, level: str = "info", **data: Any) -> None:
        """投递一个事件到界面队列。

        ``kind`` 与 ``message`` 刻意声明为**位置专用参数**（``/`` 之后才是
        可按关键字传的 ``level``）。这样调用方 ``_post("login", msg, **state)``
        即使 ``state`` 里带 ``message`` 键，也只会落进 ``data``，
        而不会撞成 ``got multiple values for argument 'message'``。

        真实教训：原先没加 ``/``，于是 ``**state``（state 里有 ``message``）
        让「检查登录状态」这个后台任务**每次都崩**，
        日志里刷满 TypeError，而界面上只是"点了没反应"。
        """
        self.events.put(Event(kind=kind, level=level, message=message, data=data))

    def _on_pipeline_event(self, event) -> None:
        """流水线回调（在工作线程里被调用）。"""
        self.events.put(
            Event(
                kind=event.kind,
                level=event.level,
                message=event.message,
                data=dict(event.data),
            )
        )

    def drain(self, limit: int = 400) -> list[Event]:
        """界面线程调用：取走当前所有待处理事件。"""
        out: list[Event] = []
        for _ in range(limit):
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out

    # ================================================================ 后台任务

    def is_busy(self, name: str) -> bool:
        return name in self._busy

    def run_async(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        on_done: Callable[[Any, Exception | None], None] | None = None,
        force: bool = False,
    ) -> bool:
        """在后台线程跑一件事，并把异常转成界面可读的事件。

        ``force=True`` 允许**同名任务重入**（用于「刷新二维码」这类需要
        "新的取代旧的"的操作）。默认仍然拒绝重入，避免用户连点造成重复副作用。

        ⚠️ 拒绝重入时**必须让调用方知道**：早先界面在 ``False`` 时什么都不做，
        于是连点「获取二维码」看起来像"点了没反应"（单次最长要等 25 秒、
        二维码 30 秒过期，用户必然连点）。现在二维码走 ``force``，
        其它任务在拒绝时也应给出界面反馈。
        """
        with self._lock:
            if name in self._busy:
                if not force:
                    return False
                # force：标记旧的已经作废（seq 机制在任务内部判断），直接放行
                log.debug("后台任务 %s 被强制重入（新请求取代旧请求）", name)
            self._busy.add(name)

        def worker() -> None:
            try:
                result = fn()
                error: Exception | None = None
            except QgbError as exc:
                result, error = None, exc
                self._post("error", exc.message, level="error", hint=exc.hint)
            except Exception as exc:
                result, error = None, exc
                log.exception("后台任务 %s 失败", name)
                self._post("error", f"{name} 失败：{type(exc).__name__}: {exc}", level="error")
            finally:
                with self._lock:
                    self._busy.discard(name)
                self._post(f"task:{name}", "已完成", done=True, ok=error is None)
                if on_done is not None:
                    try:
                        on_done(result, error)
                    except Exception:
                        log.debug("on_done 回调异常", exc_info=True)

        threading.Thread(target=worker, name=f"qgb-{name}", daemon=True).start()
        return True

    # ================================================================ 配置

    def save_settings(self) -> list[str]:
        """保存配置（**不含凭据**）。返回校验问题列表。"""
        problems = self.config.validate()
        save_config(self.config, self.config_path)
        if problems:
            self._post("error", "配置已保存，但存在待解决项：" + "；".join(problems),
                       level="warning")
        else:
            self._post("info", "设置已保存")
        self._reload_napcat()
        return problems

    def _reload_napcat(self) -> None:
        self.napcat = NapCatManager(
            self.config.napcat, self.data_dir, log_dir=self.log_dir
        )

    def validate(self) -> list[str]:
        return self.config.validate()

    # ================================================================ 构建

    def build_client(self) -> OneBotClient:
        token = self.secrets.get(KEY_QQ_ONEBOT_TOKEN) if self.secrets else None
        return OneBotClient(self.config.napcat.api_base, token)

    def build_uploader(self):
        if self.secrets is None:
            raise CredentialError("凭据库不可用，无法读取网盘授权")
        return build_uploader(self.config.upload, self.secrets)

    def _require(self, obj: Any, what: str) -> Any:
        if obj is None:
            raise QgbError(f"{what}不可用", hint="请先完成初始化或清除损坏的数据文件。")
        return obj

    # ================================================================ 监控

    def start_monitor(self) -> bool:
        """校验 → 构建 → 启动监控。"""
        if self.pipeline is not None and self.pipeline.running:
            self._post("info", "监控已在运行")
            return False

        problems = self.config.validate()
        if problems:
            self._post("error", "配置有未解决项，无法启动：" + "；".join(problems),
                       level="error", hint="请到「群与规则」页修正。")
            return False

        store = self._require(self.store, "状态库")
        try:
            uploader = self.build_uploader()
        except QgbError as exc:
            self._post("error", exc.message, level="error", hint=exc.hint)
            return False

        self.pipeline = Pipeline(
            self.config,
            store,
            self._require(self.secrets, "凭据库"),
            self.build_client(),
            uploader,
            on_event=self._on_pipeline_event,
        )
        return self.pipeline.start()

    def stop_monitor(self, *, wait: float = 20.0) -> None:
        if self.pipeline is not None:
            self.pipeline.stop(timeout=wait)

    def pause_monitor(self) -> None:
        if self.pipeline is not None:
            self.pipeline.pause()

    def resume_monitor(self) -> None:
        if self.pipeline is not None:
            self.pipeline.resume()

    def monitor_state(self) -> str:
        """返回 ``running`` / ``paused`` / ``stopped``。"""
        if self.pipeline is None or not self.pipeline.running:
            return "stopped"
        return "paused" if self.pipeline.paused else "running"

    def run_once_now(self) -> bool:
        """立即跑一轮「拉取 + 搬运」—— 就是「刷新」按钮。

        两个要点：

        1. **直接调用 ``Pipeline.run_once()``**，而不是"启动监控 → sleep →
           停止"。后者是曾经的实现，它**根本跑不完一轮**：循环还没来得及
           执行就被停掉了，按钮点了等于没点（用户反馈"有点鸡肋"就是这个原因）。

        2. **常驻监控运行时也允许手动刷新**。流水线内部有可重入锁，
           两个循环不会互相踩；而"监控中想要立刻看到新文件"是很自然的需求
           （常驻轮询要等一个 ``poll_interval_sec`` 周期）。
        """
        pipeline = self.pipeline
        if pipeline is None:
            self._post("warning", "QQ 组件未就绪，无法立即检查")
            return False

        interval = self.config.monitor.poll_interval_sec

        def task():
            try:
                stats = pipeline.run_once()
            except Exception as exc:
                self._post(
                    "error",
                    f"立即检查失败：{type(exc).__name__}: {exc}",
                    hint="可查看日志了解详情；若提示 QQ 未登录，请先到「QQ 登录」页扫码。",
                )
                return None

            self._post(
                "stats",
                (
                    f"本轮检查完成：发现 {stats.discovered} · 过滤 {stats.filtered}"
                    f" · 跳过 {stats.skipped} · 上传 {stats.uploaded}"
                    f" · 失败 {stats.failed}"
                ),
                **stats.as_dict(),
            )
            return stats

        if not self.run_async("run_once", task):
            return False

        if self.monitor_state() != "stopped":
            self._post(
                "info",
                f"已触发一次立即检查（常驻监控仍每 {interval} 秒自动检查一次）",
            )
        return True

    # ================================================================ 下载顺序

    def queue_ordered(self, limit: int = 500) -> list[dict[str, Any]]:
        """待下载队列（**已按下载顺序**排好）。

        顺序规则：置顶 → 手工顺序 → 未排序按发现时间（先入先出）。
        界面按这个顺序展示，用户调完再整体写回，顺序即下载顺序。
        """
        if self.store is None:
            return []
        return self.store.pending_ordered(limit=limit)

    def _write_queue_order(self, rows: list[dict[str, Any]]) -> int:
        if self.store is None:
            return 0
        items = [(str(r["group_id"]), int(r["busid"]), str(r["file_id"])) for r in rows]
        return self.store.set_file_order(items)

    def _rebase(self) -> list[dict[str, Any]]:
        """把当前队列按显示顺序**重写一遍** manual_order。

        为什么要整体重写：显示顺序是「置顶 → 手工顺序 → 未排序」三段合成的，
        重写后三段合成为单一序列，之后的移动只需交换相邻两项，语义最简单。
        """
        rows = self.queue_ordered()
        self._write_queue_order(rows)
        return self.queue_ordered()

    @staticmethod
    def _key(row: dict[str, Any]) -> tuple[str, int, str]:
        return (str(row["group_id"]), int(row["busid"]), str(row["file_id"]))

    def queue_move(self, key: tuple[str, int, str], delta: int) -> bool:
        """把某项在队列里上移/下移一位（``delta=-1`` 上移，``+1`` 下移）。

        只在**同组**（置顶 / 非置顶）内交换，且不跨越置顶边界 ——
        否则"上移"会把置顶项挤下去，与置顶的语义冲突。
        """
        rows = self._rebase()
        index = next((i for i, r in enumerate(rows) if self._key(r) == key), None)
        if index is None:
            return False
        pinned = [bool(r.get("pinned")) for r in rows]
        target = index + delta
        if target < 0 or target >= len(rows):
            return False
        if pinned[target] != pinned[index]:
            return False                      # 不跨越置顶边界
        rows[index], rows[target] = rows[target], rows[index]
        self._write_queue_order(rows)
        return True

    def queue_pin(self, key: tuple[str, int, str], pinned: bool) -> bool:
        """置顶 / 取消置顶。

        * 置顶：插到队列最前，并标记 pinned（之后排序始终在最前）。
        * 取消置顶：只清 pinned 标记，**位置留在未置顶段的最前**——
          不会掉到队尾（用户期待的是"让出置顶区"，不是"排到最后"）。
        """
        rows = self._rebase()
        index = next((i for i, r in enumerate(rows) if self._key(r) == key), None)
        if index is None:
            return False

        row = rows.pop(index)
        if pinned:
            rows.insert(0, row)
        else:
            first_unpinned = next(
                (i for i, r in enumerate(rows) if not r.get("pinned")), len(rows)
            )
            rows.insert(first_unpinned, row)

        self._write_queue_order(rows)
        if self.store is not None:
            self.store.set_pinned(*self._key(row), pinned=pinned)
        return True

    def queue_to_front(self, key: tuple[str, int, str]) -> bool:
        """置顶到第一位（与 queue_pin(pinned=True) 等价，便于界面直连）。"""
        return self.queue_pin(key, pinned=True)

    def requeue_failed(self) -> int:
        store = self.store
        if store is None:
            return 0
        n = store.requeue()
        self._post("info", f"已把 {n} 个失败项重新排队")
        return n

    def stats(self) -> dict[str, Any]:
        base = {
            "state": self.monitor_state(),
            "seen": 0,
            "counts": {},
            "bytes_text": "0 B",
            "uptime": 0.0,
        }
        if self.store is not None:
            try:
                base["seen"] = self.store.seen_count()
                base["counts"] = self.store.stats()
            except QgbError:
                pass
        if self.pipeline is not None:
            base["bytes_text"] = self.pipeline.stats.as_dict()["bytes_text"]
            base["uptime"] = self.pipeline.stats.as_dict()["uptime"]
        return base

    def recent_records(self, limit: int = 200) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        try:
            return self.store.recent(limit=limit)
        except QgbError:
            return []

    # ================================================================ NapCat

    def napcat_status(self) -> dict[str, Any]:
        if self.napcat is None:
            return {"installed": False, "running": False, "install_dir": "", "note": "未初始化"}
        try:
            return self.napcat.status().as_dict()
        except Exception as exc:
            return {"installed": False, "running": False, "install_dir": "",
                    "note": f"状态读取失败：{type(exc).__name__}"}

    def start_napcat(self) -> bool:
        def task():
            assert self.napcat is not None
            # 启动前预置配置：NapCat 默认**不开启** OneBot HTTP 服务，
            # 不预置就得让用户去网页端手点「新建 HTTP 服务器」——
            # 那是整个上手里最容易卡住的一步。
            try:
                self.ensure_napcat_configs()
            except Exception as exc:
                log.warning("预置 NapCat 配置失败：%s", type(exc).__name__)

            status = self.napcat.start(client=self.build_client())
            self._post("napcat", "QQ 组件已启动", **status.as_dict())
            return status

        return self.run_async("napcat_start", task)

    def stop_napcat(self) -> bool:
        def task():
            assert self.napcat is not None
            self.napcat.stop()
            self._post("napcat", "QQ 组件已停止", **self.napcat_status())
            return None

        return self.run_async("napcat_stop", task)

    def install_napcat_from_zip(self, zip_path: str) -> bool:
        def task():
            assert self.napcat is not None
            dest = self.napcat.install_from_zip(zip_path)
            self._post("napcat", "QQ 组件安装完成", path=str(dest), **self.napcat_status())
            return dest

        return self.run_async("napcat_install", task)

    def napcat_install_hint(self) -> str:
        return self.napcat.install_instructions() if self.napcat else ""

    def discover_webui_token(self) -> bool:
        """从 NapCat 本地配置读取 WebUI 令牌并加密保存。"""
        def task():
            assert self.napcat is not None
            token = self.napcat.discover_webui_token()
            if token and self.secrets is not None:
                self.secrets.set(KEY_NAPCAT_WEBUI_TOKEN, token)
                self._post("info", "已自动读取并加密保存 WebUI 令牌")
                return True
            self._post("info", "未在 NapCat 配置中找到 WebUI 令牌，请手动填写", level="warning")
            return False

        return self.run_async("napcat_token", task)

    def fetch_qrcode(self) -> bool:
        """获取/刷新二维码。

        **为什么不能让重复点击被静默丢弃**：``run_async`` 原本对同名任务会
        ``return False``（"已有同名任务在跑"），而界面在拿到 False 时什么都不做。
        单次取码最长要等 25 秒（HTTP 超时），二维码又只有 30 秒时效 ——
        用户会连点数次，每一次都被丢弃，表现就是「点了没反应/刷新不了」。

        现在改成**取代语义**：新请求作废旧请求，旧请求即使拿到结果也会被丢弃
        （``seq`` 不匹配），界面永远只显示最新那一次的结果。
        """
        self._qr_seq += 1
        seq = self._qr_seq

        def task():
            webui = self.config.napcat.webui_base
            # 令牌以组件配置为准（见 _webui_token 的说明）：
            # 切换运行方式后旧令牌会失效，用旧值只会得到
            # 「令牌无效」+ 触发 login rate limit。
            token = self._webui_token()

            install_dir = self.napcat.install_dir if self.napcat else None
            qr = fetch_qrcode(webui, token or "", napcat_dir=install_dir)

            if seq != self._qr_seq:
                # 已被更新的请求取代：不要用过期结果覆盖界面
                log.debug("二维码请求 %s 已被更新的一次取代，丢弃结果", seq)
                return qr

            self._qr = qr
            self._qr_seq_ok = seq
            self._qr_at = time.monotonic()
            if qr.ok:
                self._post("qr", "二维码已获取，请用手机 QQ 扫码", ok=True,
                           source=qr.source, seq=seq)
            else:
                self._post(
                    "qr",
                    qr.error or "二维码获取失败",
                    level="warning",
                    ok=False,
                    hint=qr.hint,
                    seq=seq,
                )
            return qr

        if not self.run_async("qrcode", task, force=True):
            return False
        # 立刻回一条事件，让界面马上有反馈（不等网络请求完成）
        self._post("qr", "正在获取二维码…", level="info", pending=True, seq=seq)
        return True

    @property
    def qrcode_age_sec(self) -> float:
        """上一次成功取到二维码距今多少秒（用于提示是否已过期）。"""
        if not self._qr_at:
            return -1.0
        return max(0.0, time.monotonic() - self._qr_at)

    @property
    def qrcode(self) -> QrCode:
        return self._qr

    def _webui_token(self) -> str:
        """解析 NapCat WebUI 令牌。

        **以组件自己的配置文件为准，凭据库只当缓存。**

        为什么不能让凭据库优先：切换 QQ 组件运行时（挂钩已装 QQ ↔ 自带运行时）
        之后，NapCat 会重新生成令牌，而旧令牌仍留在凭据库里。程序若继续用旧值，
        就会一直报「WebUI 令牌无效」，反复重试还会触发 NapCat 的
        ``login rate limit`` —— 实测踩到过，症状很有迷惑性
        （配置明明是对的，程序就是不认）。

        发现不一致时会就地更新凭据库，并告知用户。
        """
        from_config = ""
        if self.napcat is not None:
            try:
                from_config = self.napcat.read_webui_token_from_config()
            except Exception as exc:
                log.warning("读取 NapCat WebUI 令牌失败：%s", type(exc).__name__)

        saved = (self.secrets.get(KEY_NAPCAT_WEBUI_TOKEN) if self.secrets else "") or ""

        if from_config:
            if from_config != saved and self.secrets is not None:
                try:
                    self.secrets.set(KEY_NAPCAT_WEBUI_TOKEN, from_config)
                    self._post(
                        "info",
                        "检测到 QQ 组件令牌已变化（多半是切换过运行方式），已自动更新本机凭据",
                    )
                except Exception as exc:
                    log.warning("更新 WebUI 令牌失败：%s", type(exc).__name__)
            return from_config

        # 始终返回字符串：调用方会直接拿去做 URL 拼接与鉴权
        return saved or ""

    def qq_login_state(self) -> dict[str, Any]:
        """查询 QQ 登录状态。

        **优先走 NapCat WebUI 的 CheckLoginStatus**：它在「尚未扫码」时也能
        正常返回，而 OneBot 的 get_login_info 在未登录时直接报错，无法区分
        「组件没起来」与「还没扫码」——这对界面给出正确提示很关键。
        两者都不通时才返回 unknown。
        """
        token = self._webui_token()
        status = check_login(self.config.napcat.webui_base, token or "")
        if status.reachable:
            return {
                "category": status.category,
                "message": status.message,
                "login_error": status.login_error,
                "source": "webui",
            }

        try:
            info = self.build_client().get_login_info()
        except QgbError:
            return {
                "category": "unknown",
                "message": status.message or "NapCat 未就绪",
                "source": "none",
            }
        return {
            "category": info.category,
            "message": info.message,
            "user_id": info.user_id,
            "source": "onebot",
        }

    def cached_group_names(self) -> dict[str, str]:
        """已缓存的「群号 → 群名」（**不发网络请求**，供界面即时预览）。

        合并两处来源：门面层自己拉的，以及流水线在运行中拉的 ——
        谁先拿到就用谁的，避免重复请求。
        """
        merged = dict(self._group_names)
        if self.pipeline is not None:
            try:
                merged.update(self.pipeline.group_names())
            except Exception:
                pass
        return merged

    def load_group_names(self) -> bool:
        """异步拉取群名（界面「按群名一键生成」用）。

        刻意**不抛异常**：拿不到群名只意味着目录名退回群号，
        不该打断用户的操作。
        """

        def task():
            try:
                groups = self.build_client().get_group_list()
            except Exception as exc:
                self._post(
                    "group_names",
                    f"读取群名失败：{type(exc).__name__}",
                    level="warning",
                    names={},
                )
                return {}

            names: dict[str, str] = {}
            for item in groups or []:
                if isinstance(item, dict):
                    gid = str(item.get("group_id") or "")
                    if gid:
                        names[gid] = str(item.get("group_name") or "")

            self._group_names.update(names)
            self._post("group_names", f"已读取 {len(names)} 个群名", names=names)
            return names

        return self.run_async("group_names", task)

    def napcat_webui_url(self) -> str:
        """NapCat 网页版地址（供「打开网页版」按钮使用）。

        令牌只在内存里拼进 URL，**不写日志、不落盘**。
        """
        return webui_url(self.config.napcat.webui_base, self._webui_token() or "")

    def ensure_napcat_configs(self) -> dict[str, Any]:
        """预置 NapCat 配置（WebUI + OneBot HTTP），并把令牌存进加密凭据库。

        这样用户不必进 NapCat 网页端手工「新建 HTTP 服务器」——
        默认配置里那个服务是**关闭**的，是上手时最容易卡住的一步。
        """
        if self.napcat is None:
            return {}
        result = self.napcat.ensure_configs(
            webui_port=self._port_of(self.config.napcat.webui_base, 6099),
            onebot_port=self._port_of(self.config.napcat.api_base, 3000),
        )
        token = str(result.get("webui_token") or "")
        if token and self.secrets is not None:
            self.secrets.set(KEY_NAPCAT_WEBUI_TOKEN, token)

        created = result.get("created") or []
        if created:
            self._post("info", f"已自动预置 {len(created)} 个 NapCat 配置文件")
        return result

    @staticmethod
    def _port_of(url: str, default: int) -> int:
        try:
            from urllib.parse import urlparse

            return int(urlparse(url or "").port or default)
        except (ValueError, TypeError):
            return default

    def probe_qq(self) -> bool:
        def task():
            state = self.qq_login_state()
            # state 里自带 message 键；_post 的 kind/message 是位置专用参数，
            # 所以这里显式取值传给 message，其余键照常进 data。
            self._post("login", f"QQ 状态：{state.get('message', '未知')}", **state)
            return state

        return self.run_async("qq_probe", task)

    # ================================================================ 网盘

    def adapter_choices(self) -> list[tuple[str, str]]:
        return available_adapters()

    def save_webdav_credentials(self, username: str, password: str) -> None:
        if self.secrets is None:
            raise CredentialError("凭据库不可用")
        values: dict[str, str] = {}
        if username:
            values[KEY_NETDISK_WEBDAV_USERNAME] = username
        if password:
            values[KEY_NETDISK_WEBDAV_PASSWORD] = password
        if values:
            self.secrets.set_many(values)
        self._post("creds", "网盘凭据已加密保存", **{"count": len(values)})

    def clear_webdav_credentials(self) -> None:
        if self.secrets is None:
            return
        self.secrets.delete(KEY_NETDISK_WEBDAV_USERNAME)
        self.secrets.delete(KEY_NETDISK_WEBDAV_PASSWORD)
        self._post("creds", "已清除网盘凭据")

    def test_netdisk(self) -> bool:
        def task():
            uploader = self.build_uploader()
            ok, message = uploader.test()
            self._post(
                "netdisk",
                f"{'连接正常' if ok else '连接失败'}：{message}",
                level="info" if ok else "error",
                ok=ok,
                hint="" if ok else "请检查地址、账号密码，以及 OpenList/后端是否在运行。",
            )
            return ok

        return self.run_async("netdisk_test", task)

    # ================================================================ 打开网盘

    def netdisk_clients(self) -> list[dict[str, Any]]:
        """列出本机已安装的网盘客户端（供界面展示）。

        同步调用：只读注册表与少量目录，很快，不需要后台线程。
        """
        try:
            clients = detect_clients()
        except Exception as exc:  # 探测失败不该让界面崩
            log.warning("探测网盘客户端失败：%s", type(exc).__name__)
            return []
        return [
            {
                "key": c.key,
                "name": c.name,
                "exe": c.exe,
                "install_dir": c.install_dir,
                "web_url": c.web_url,
                "installed": c.installed,
                "source": c.source,
                "sync_dirs": list(c.sync_dirs),
                "display_path": c.display_path(),
            }
            for c in clients
        ]

    def open_netdisk(self, key: str = "baidu") -> bool:
        """打开网盘：装了客户端就开客户端，没装就开网页版。

        之所以是「打开」而不是「上传」：上传由流水线按配置的适配器完成，
        这个按钮的作用是让用户**立刻看到文件到底传上去了没有**。
        客户端路径是自动探测的，用户把网盘装在哪个盘都能找到。
        """

        def task():
            result = open_client_or_web(key)
            opened = str(result.get("opened") or "none")
            name = str(result.get("name") or key)

            if opened == "client":
                self._post(
                    "netdisk",
                    f"已打开{name}客户端",
                    opened="client",
                    **{"path": result.get("exe", "")},
                )
            elif opened == "web":
                self._post(
                    "netdisk",
                    f"本机没找到{name}客户端，已在浏览器打开网页版",
                    level="info",
                    opened="web",
                    path=str(result.get("target") or ""),
                )
            else:
                self._post(
                    "netdisk",
                    f"无法打开{name}",
                    level="warning",
                    opened="none",
                    hint="请确认系统默认浏览器可用；或手动访问网页版。",
                )
            return result

        return self.run_async("netdisk_open", task)

    # ================================================================ 凭据

    def credentials_overview(self) -> list[dict[str, Any]]:
        if self.secrets is None:
            return []
        try:
            return self.secrets.describe()
        except QgbError:
            return []

    def credentials_health(self) -> dict[str, Any]:
        if self.secrets is None:
            return {"backend": "不可用", "path": str(default_store_path()),
                    "exists": False, "count": 0}
        try:
            return self.secrets.health()
        except QgbError as exc:
            return {"backend": f"异常：{exc.message}", "path": str(default_store_path()),
                    "exists": False, "count": 0}

    def clear_all_credentials(self) -> None:
        if self.secrets is None:
            return
        self.secrets.clear()
        self._post("creds", "已清除本机全部凭据")

    def open_path(self, which: str) -> str:
        """解析一个「打开目录」的目标，返回可交给资源管理器的路径。

        既支持预设键名（``data`` / ``logs`` / ``temp`` / ``config``），
        也支持**直接传一个已存在的绝对目录**（例如网盘同步文件夹）。
        目录不存在时返回空串，界面据此提示而不是打开一个无效位置。
        """
        mapping = {
            "data": Path(self.paths["data_dir"]),
            "logs": self.log_dir,
            "temp": self.config.resolved_temp_dir(),
            "config": self.config_path,
        }
        target = mapping.get(which)
        if target is not None:
            return str(target)

        # 直接传路径的情况：必须真实存在，避免把用户带到不存在的位置
        try:
            candidate = Path(which)
            if candidate.is_dir():
                return str(candidate)
        except (OSError, ValueError):
            pass
        return ""

    def health(self) -> dict[str, Any]:
        """整体自检，给「诊断」用。"""
        return {
            "config_ok": not self.config.validate(),
            "problems": self.config.validate(),
            "credentials": self.credentials_health(),
            "napcat": self.napcat_status(),
            "monitor": self.monitor_state(),
            "adapter": self.config.upload.adapter,
            "target": self.config.upload.remote_root,
            "groups": len(self.config.groups),
            "data_dir": str(self.data_dir),
            "data_dir_source": data_dir_source(),
            "state_db": str(self.data_dir / "state.db"),
            "log_dir": str(self.log_dir),
        }

    # ================================================================ 关闭

    def shutdown(self) -> None:
        try:
            self.stop_monitor(wait=6.0)
        except Exception:
            pass

        # 关掉由本程序启动的 OpenList（自己手动开的实例不会被碰）
        try:
            if self.openlist is not None:
                self.openlist.stop()
        except Exception:
            pass

        for obj in (self.store,):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass


def state_counts_summary(counts: dict[str, int]) -> str:
    """把状态计数转成一行人类可读文本。"""
    labels = {
        TransferState.DISCOVERED.value: "待处理",
        TransferState.DOWNLOADING.value: "下载中",
        TransferState.DOWNLOADED.value: "已下载",
        TransferState.UPLOADING.value: "上传中",
        TransferState.UPLOADED.value: "已上传",
        TransferState.DONE.value: "已完成",
        TransferState.FAILED.value: "失败",
        TransferState.FILTERED_OUT.value: "已过滤",
        TransferState.SKIPPED.value: "已跳过",
        TransferState.EXPIRED.value: "已过期",
    }
    parts = [
        f"{labels.get(k, k)} {counts[k]}"
        for k in labels
        if counts.get(k)
    ]
    return " · ".join(parts) if parts else "暂无记录"
