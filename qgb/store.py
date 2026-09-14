"""SQLite 状态库：去重主键、任务状态、运行期 KV。

为什么用 SQLite 而不是 ``config.json``：
  * 去重写入是**高频事务**，JSON 全量重写既慢又容易损坏
  * 崩溃后可安全恢复（WAL 模式）
  * 天然支持按状态查询「还有哪些没搬完」
"""

from __future__ import annotations

import logging

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .errors import QgbError
from .models import GroupFile, TransferState

log = logging.getLogger(__name__)

__all__ = ["StateStore", "StoreError"]

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS files (
    group_id      TEXT    NOT NULL,
    busid         INTEGER NOT NULL,
    file_id       TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    size          INTEGER NOT NULL DEFAULT 0,
    upload_time   INTEGER NOT NULL DEFAULT 0,
    uploader_name TEXT    NOT NULL DEFAULT '',
    folder_id     TEXT    NOT NULL DEFAULT '/',
    first_seen    REAL    NOT NULL,
    last_seen     REAL    NOT NULL,
    PRIMARY KEY (group_id, busid, file_id)
);

CREATE TABLE IF NOT EXISTS transfers (
    group_id    TEXT    NOT NULL,
    busid       INTEGER NOT NULL,
    file_id     TEXT    NOT NULL,
    name        TEXT    NOT NULL,
    size        INTEGER NOT NULL DEFAULT 0,
    local_path  TEXT    NOT NULL DEFAULT '',
    remote_path TEXT    NOT NULL DEFAULT '',
    sha256      TEXT    NOT NULL DEFAULT '',
    state       TEXT    NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT    NOT NULL DEFAULT '',
    manual_order INTEGER NOT NULL DEFAULT 0,
    pinned       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    PRIMARY KEY (group_id, busid, file_id)
);

CREATE INDEX IF NOT EXISTS idx_transfers_state   ON transfers(state);
CREATE INDEX IF NOT EXISTS idx_transfers_updated ON transfers(updated_at);
CREATE INDEX IF NOT EXISTS idx_transfers_name ON transfers(group_id, name, size);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL    NOT NULL,
    level     TEXT    NOT NULL,
    group_id  TEXT    NOT NULL DEFAULT '',
    name      TEXT    NOT NULL DEFAULT '',
    message   TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
"""


class StoreError(QgbError):
    title = "本地状态库异常"
    hint = "请确认磁盘空间与目录权限；必要时删除 state.db 后重新运行（会重新扫描已有文件）。"


class StateStore:
    """线程安全的状态库封装。"""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(_DDL)
            self._migrate()
            self._conn.commit()
            self._conn.execute(
                "INSERT OR IGNORE INTO kv(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"无法打开状态库：{exc}") from exc

    # -------------------------------------------------- 基础设施

    #: 后加的列：``CREATE TABLE IF NOT EXISTS`` **不会**给已存在的表补列，
    #: 所以老库必须显式 ALTER，否则升级后一执行带新列的 SQL 就报 no such column。
    _ADDED_COLUMNS = (
        ("transfers", "manual_order", "INTEGER NOT NULL DEFAULT 0"),
        ("transfers", "pinned", "INTEGER NOT NULL DEFAULT 0"),
        #: 冷却截止时间（unix 秒）。临时性故障（如 NapCat 的 fileUUID 未就绪）
        #: 用完必须设一个冷却，否则它**立刻又排到队首**、把新一轮尝试全吃掉。
        #: 实测踩过：几十条旧记录每轮都失败且不消耗重试次数，队首永远被它们占着，
        #: 新文件一直轮不到 —— 用户看到的就是"下载始终不开始"。
        ("transfers", "retry_at", "REAL NOT NULL DEFAULT 0"),
        #: 连续多少次"完整列表里确实没有这个文件"。达到上限就标记为已失效，
        #: 不再无限重试 —— 否则被删除的历史记录会永远占着队列（用户明确要求不要累加）。
        ("transfers", "absent_count", "INTEGER NOT NULL DEFAULT 0"),
    )

    def _migrate(self) -> None:
        """把老库补齐到当前结构（幂等，可反复执行）。"""
        for table, column, decl in self._ADDED_COLUMNS:
            cols = {
                row["name"]
                for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not cols:
                continue                     # 表还不存在，_DDL 已建好且已含该列
            if column not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                log.info("状态库迁移：%s 增加列 %s", table, column)

    # -------------------------------------------------- 手工排序

    def set_file_order(
        self,
        items: list[tuple[str, int, str]],
        *,
        pinned: bool | None = None,
    ) -> int:
        """按给定顺序写入 ``manual_order``（下标 × 10，留出插入余地）。

        ``items`` 为 ``[(group_id, busid, file_id), ...]``，**按用户期望的顺序**排列。
        ``pinned`` 为 None 时不动置顶标记。
        """
        n = 0
        with self._write() as conn:
            for index, (group_id, busid, file_id) in enumerate(items):
                if pinned is None:
                    cur = conn.execute(
                        "UPDATE transfers SET manual_order=? "
                        "WHERE group_id=? AND busid=? AND file_id=?",
                        ((index + 1) * 10, str(group_id), int(busid), str(file_id)),
                    )
                else:
                    cur = conn.execute(
                        "UPDATE transfers SET manual_order=?, pinned=? "
                        "WHERE group_id=? AND busid=? AND file_id=?",
                        ((index + 1) * 10, 1 if pinned else 0,
                         str(group_id), int(busid), str(file_id)),
                    )
                n += cur.rowcount or 0
            conn.commit()
        return n

    def set_pinned(self, group_id: str, busid: int, file_id: str, pinned: bool) -> None:
        with self._write() as conn:
            conn.execute(
                "UPDATE transfers SET pinned=? WHERE group_id=? AND busid=? AND file_id=?",
                (1 if pinned else 0, str(group_id), int(busid), str(file_id)),
            )
            conn.commit()

    def pending_ordered(self, limit: int = 500) -> list[dict[str, Any]]:
        """待处理队列，按**手工顺序**排：置顶在前 → ``manual_order`` → 发现时间。

        ``manual_order`` 默认 0，表示"没被手工排过"，排在已排序的之后、
        仍按先入先出。这样既满足"完全按我排的顺序"，又不会让没排过的文件乱跳。
        """
        with self._lock:
            # ⚠️ 不能直接 ``ORDER BY manual_order``：未手工排过的行 manual_order=0，
            #    而 0 < 10，会让它们插到已排序的行**中间**。
            #    正确做法：没排过（=0）的当作"极大值"排到最后，仍按发现时间先入先出。
            now = time.time()
            cur = self._conn.execute(
                # 冷却中的（retry_at > now）先跳过，让其它文件有机会被处理
                "SELECT * FROM transfers WHERE state=? AND retry_at<=? "
                "ORDER BY pinned DESC, "
                "         CASE WHEN manual_order>0 THEN manual_order ELSE 2147483647 END, "
                "         created_at, rowid "
                "LIMIT ?",
                (TransferState.DISCOVERED.value, now, int(limit)),
            )
            return [dict(r) for r in cur.fetchall()]

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except sqlite3.Error as exc:
                self._conn.rollback()
                raise StoreError(f"写入状态库失败：{exc}") from exc

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -------------------------------------------------- 文件台账

    def upsert_file(self, gf: GroupFile) -> None:
        """记录「见过这个文件」。已存在则只刷新 last_seen。"""
        now = time.time()
        row = gf.to_row()
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO files (group_id, busid, file_id, name, size, upload_time,
                                   uploader_name, folder_id, first_seen, last_seen)
                VALUES (:group_id, :busid, :file_id, :name, :size, :upload_time,
                        :uploader_name, :folder_id, :first_seen, :last_seen)
                ON CONFLICT(group_id, busid, file_id) DO UPDATE SET
                    name = excluded.name,
                    size = excluded.size,
                    upload_time = excluded.upload_time,
                    uploader_name = excluded.uploader_name,
                    folder_id = excluded.folder_id,
                    last_seen = excluded.last_seen
                """,
                {**row, "first_seen": now, "last_seen": now},
            )

    def seen_count(self) -> int:
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM files")
            return int(cur.fetchone()["n"])

    # -------------------------------------------------- 任务 / 去重

    def get_transfer(self, key: tuple[str, int, str]) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM transfers WHERE group_id=? AND busid=? AND file_id=?",
                (key[0], int(key[1]), str(key[2])),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def is_settled(self, key: tuple[str, int, str]) -> bool:
        """该文件是否已经处理完结（用于去重跳过）。"""
        rec = self.get_transfer(key)
        if not rec:
            return False
        return rec["state"] in (
            TransferState.DONE.value,
            TransferState.UPLOADED.value,
            TransferState.SKIPPED.value,
            TransferState.FILTERED_OUT.value,
        )

    def is_uploaded(self, key: tuple[str, int, str]) -> bool:
        rec = self.get_transfer(key)
        return bool(rec) and rec["state"] in (
            TransferState.DONE.value,
            TransferState.UPLOADED.value,
        )

    def current_file_id(self, group_id: str, name: str, size: int) -> str:
        """从 ``files`` 表取该文件**当前**的 file_id（按 群+名+大小 匹配）。

        ``files`` 表每次扫描都会 upsert 刷新，而 ``transfers`` 里的 file_id
        是登记那一刻固定的 —— 两者会不同步：同一文件在群里换过一次 file_id 后，
        transfers 里仍是旧值，取直链就会失败（NapCat 报 fileUUID not found）。
        这里优先用 files 表里的新值。
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT file_id FROM files WHERE group_id=? AND name=? AND size=? "
                "ORDER BY last_seen DESC LIMIT 1",
                (str(group_id), str(name), int(size)),
            )
            row = cur.fetchone()
            return str(row["file_id"]) if row else ""

    def set_file_id(self, key: tuple[str, int, str], new_file_id: str) -> bool:
        """把 transfer 行的 file_id 换成当前有效值（主键的一部分，需重建行）。

        主键是 (group_id, busid, file_id)，改 file_id 等于换一行：先把旧行搬到
        新主键下（保留状态、attempts、排序、置顶），再删除旧行。若新主键已存在
        则直接删掉旧行（避免主键冲突）。
        """
        old = (str(key[0]), int(key[1]), str(key[2]))
        new = (old[0], old[1], str(new_file_id))
        if old == new:
            return False
        with self._write() as conn:
            exists = conn.execute(
                "SELECT 1 FROM transfers WHERE group_id=? AND busid=? AND file_id=?",
                new,
            ).fetchone()
            if exists:
                conn.execute(
                    "DELETE FROM transfers WHERE group_id=? AND busid=? AND file_id=?", old
                )
            else:
                conn.execute(
                    "UPDATE transfers SET file_id=?, updated_at=? "
                    "WHERE group_id=? AND busid=? AND file_id=?",
                    (new[2], time.time(), old[0], old[1], old[2]),
                )
            conn.commit()
        return True

    def is_uploaded_like(self, group_id: str, name: str, size: int) -> bool:
        """按 **(群, 文件名, 大小)** 判断是否已搬过。

        为什么按名字而不是 file_id：QQ 群文件的 ``file_id`` **不是稳定标识**
        （换一次会话/组件重启就可能变），于是同一个文件会被当成新文件重新
        下载上传 —— 用户实测反馈"上传过的为什么还重复扫描上传"。
        (群 + 文件名 + 大小) 在同一群里足以唯一确定一个文件。

        ⚠️ 只认 **DONE / UPLOADED**（真正搬成功的）。早先把 SKIPPED 也算进来，
        结果把「被过滤掉」的文件也提前跳过了 —— 它们本该每轮重新评估过滤规则
        （改了过滤条件后要能重新纳入），于是 ``test_non_matching_file_is_filtered``
        这类用例直接挂掉。**去重的语义是"搬过了"，不是"见过了"。**
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT 1 FROM transfers WHERE group_id=? AND name=? AND size=? "
                "AND state IN (?,?) LIMIT 1",
                (str(group_id), str(name), int(size),
                 TransferState.DONE.value, TransferState.UPLOADED.value),
            )
            return cur.fetchone() is not None

    def claim(self, gf: GroupFile) -> bool:
        """把文件登记为新任务。

        返回 ``True`` 表示「本次由我负责处理」；``False`` 表示已有未完结记录
        （可能正在被处理，或已完结），调用方应跳过。
        """
        now = time.time()
        with self._write() as conn:
            cur = conn.execute(
                """
                INSERT INTO transfers (group_id, busid, file_id, name, size, state,
                                       created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(group_id, busid, file_id) DO NOTHING
                """,
                (
                    gf.group_id,
                    int(gf.busid),
                    str(gf.file_id),
                    gf.name,
                    int(gf.size),
                    TransferState.DISCOVERED.value,
                    now,
                    now,
                ),
            )
            return cur.rowcount == 1

    def mark(
        self,
        key: tuple[str, int, str],
        state: TransferState,
        *,
        local_path: str | None = None,
        remote_path: str | None = None,
        sha256: str | None = None,
        error: str | None = None,
        bump_attempts: bool = False,
        retry_after: float | None = None,
        bump_absent: bool = False,
        reset_absent: bool = False,
    ) -> None:
        sets = ["state=?", "updated_at=?"]
        params: list[Any] = [state.value, time.time()]
        if local_path is not None:
            sets.append("local_path=?")
            params.append(local_path)
        if remote_path is not None:
            sets.append("remote_path=?")
            params.append(remote_path)
        if sha256 is not None:
            sets.append("sha256=?")
            params.append(sha256)
        if error is not None:
            sets.append("error=?")
            params.append(error)
        if bump_attempts:
            sets.append("attempts=attempts+1")
        if bump_absent:
            sets.append("absent_count=absent_count+1")
        if reset_absent:
            sets.append("absent_count=0")
        if retry_after is not None:
            # 冷却：这段时间内不把它排进待处理队列（见 pending_ordered）
            sets.append("retry_at=?")
            params.append(time.time() + max(0.0, float(retry_after)))

        params.extend([key[0], int(key[1]), str(key[2])])
        with self._write() as conn:
            conn.execute(
                f"UPDATE transfers SET {', '.join(sets)} "
                "WHERE group_id=? AND busid=? AND file_id=?",
                params,
            )

    def reset_stale(self, states: Sequence[TransferState]) -> int:
        """把上次运行中断留下的「进行中」任务退回 DISCOVERED。"""
        if not states:
            return 0
        placeholders = ",".join("?" for _ in states)
        with self._write() as conn:
            cur = conn.execute(
                f"UPDATE transfers SET state=?, updated_at=? WHERE state IN ({placeholders})",
                [TransferState.DISCOVERED.value, time.time(), *[s.value for s in states]],
            )
            return cur.rowcount

    def reset_discovery(self) -> dict[str, int]:
        """清空「待处理 / 已过滤 / 失败」等记录，让下次扫描**重新发现**。

        为什么需要：``transfers`` 是**累计**的。一次全量扫描会把群里所有文件都
        登记成「待处理」，之后即使文件已从群里删除、或用户根本不想搬，记录也会
        永远留在队列里。实测用户库里积了 48,035 条「待处理」，最早的是**前一天**
        留下的 —— 队列一直在处理陈旧记录，真正的新文件反而轮不到。

        **保留**已完成/已上传/已跳过：它们是去重依据，删掉会导致全部重新下载。
        """
        purged = [
            "discovered", "filtered_out", "failed", "expired",
            "downloading", "downloaded", "uploading",
        ]
        placeholders = ",".join("?" for _ in purged)
        with self._write() as conn:
            before = {
                row["state"]: int(row["n"])
                for row in conn.execute(
                    "SELECT state, COUNT(*) n FROM transfers GROUP BY state"
                ).fetchall()
            }
            cur = conn.execute(
                f"DELETE FROM transfers WHERE state IN ({placeholders})", purged
            )
            conn.commit()
        removed = int(cur.rowcount or 0)
        log.info("已重置发现：清除 %s 条待处理/过滤/失败记录（保留已搬完的去重记录）", removed)
        return {"removed": removed, **{f"before_{k}": v for k, v in before.items()}}

    def requeue(self, states: Sequence[TransferState] = (TransferState.FAILED,)) -> int:
        """把失败（或指定状态）的任务重新排队，供 GUI 的「重试失败项」使用。"""
        if not states:
            return 0
        placeholders = ",".join("?" for _ in states)
        with self._write() as conn:
            cur = conn.execute(
                f"UPDATE transfers SET state=?, attempts=0, error='', updated_at=? "
                f"WHERE state IN ({placeholders})",
                [TransferState.DISCOVERED.value, time.time(), *[s.value for s in states]],
            )
            return cur.rowcount

    def pending(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM transfers WHERE state=? ORDER BY created_at LIMIT ?",
                (TransferState.DISCOVERED.value, int(limit)),
            )
            return [dict(r) for r in cur.fetchall()]

    def by_state(self, state: TransferState, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM transfers WHERE state=? ORDER BY updated_at DESC LIMIT ?",
                (state.value, int(limit)),
            )
            return [dict(r) for r in cur.fetchall()]

    def stats(self) -> dict[str, int]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM transfers GROUP BY state"
            )
            out = {r["state"]: int(r["n"]) for r in cur.fetchall()}
        for state in TransferState:
            out.setdefault(state.value, 0)
        return out

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM transfers ORDER BY updated_at DESC LIMIT ?", (int(limit),)
            )
            return [dict(r) for r in cur.fetchall()]

    # -------------------------------------------------- 运行期 KV

    def list_files(self, *, search: str = "", limit: int = 2000) -> list[dict[str, Any]]:
        """全量文件列表（transfers 联 files 取上传时间），支持按文件名/群号搜索。

        为什么要联表：``upload_time``（文件在群里上传的时间）在 ``files`` 表，
        而状态在 ``transfers`` 表 —— 界面要能"按上传时间排序"，就必须一起取。
        """
        sql = (
            "SELECT t.*, f.upload_time AS upload_time, f.uploader_name AS uploader_name "
            "FROM transfers t LEFT JOIN files f "
            "  ON f.group_id=t.group_id AND f.busid=t.busid AND f.file_id=t.file_id "
        )
        params: list[Any] = []
        term = (search or "").strip()
        if term:
            sql += "WHERE t.name LIKE ? OR t.group_id LIKE ? "
            params.extend([f"%{term}%", f"%{term}%"])
        sql += "ORDER BY t.updated_at DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            cur = self._conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def set_kv(self, key: str, value: str) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO kv(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)),
            )

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            cur = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    # -------------------------------------------------- 永久排除

    #: kv 里存永久排除名单的键（每行一个 "群号\x1fbusid\x1ffile_id"）
    _KV_EXCLUDED = "excluded_permanent"

    def excluded_keys(self) -> set[tuple[str, int, str]]:
        """读取永久排除名单。"""
        raw = self.get_kv(self._KV_EXCLUDED, "") or ""
        out: set[tuple[str, int, str]] = set()
        for line in raw.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                try:
                    out.add((parts[0], int(parts[1]), parts[2]))
                except ValueError:
                    continue
        return out

    def is_excluded(self, key: tuple[str, int, str]) -> bool:
        return key in self.excluded_keys()

    def exclude(self, keys: list[tuple[str, int, str]], *, reason: str = "永久排除") -> int:
        """把文件加入永久排除名单（并标成 SKIPPED 让它不再被扫描纳入）。

        为什么要永久排除：有些文件永远搬不上去（例如 QQ 侧取不到直链的 -134、
        超大文件被上游拒收）。如果只是普通失败，它会永远留在队列里反复重试、
        反复下载，白耗流量。永久排除后它既不出现在待处理里，也不会再被重新发现。
        """
        current = self.excluded_keys()
        added = 0
        for k in keys:
            if k in current:
                continue
            current.add(k)
            added += 1
            # SKIPPED 属于 is_settled()，扫描时会被跳过（与"已处理完结"同义）
            self.mark(k, TransferState.SKIPPED, error=reason)
        if added:
            self.set_kv(self._KV_EXCLUDED,
                        "\n".join(f"{g}\x1f{b}\x1f{f}" for g, b, f in sorted(current)))
        return added

    def unexclude(self, keys: list[tuple[str, int, str]] | None = None) -> int:
        """解除永久排除；不传 keys 表示全部解除。返回解除数量。"""
        current = self.excluded_keys()
        if keys is None:
            removed = len(current)
            self.set_kv(self._KV_EXCLUDED, "")
            return removed
        removed = 0
        for k in keys:
            if k in current:
                current.discard(k)
                removed += 1
        self.set_kv(self._KV_EXCLUDED,
                    "\n".join(f"{g}\x1f{b}\x1f{f}" for g, b, f in sorted(current)))
        return removed

    # -------------------------------------------------- 事件流水

    def add_event(self, level: str, message: str, *, group_id: str = "", name: str = "") -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO events(ts, level, group_id, name, message) VALUES(?,?,?,?,?)",
                (time.time(), level, group_id, name, message),
            )

    def events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            return [dict(r) for r in cur.fetchall()]

    def prune_events(self, keep: int = 5000) -> None:
        with self._write() as conn:
            conn.execute(
                "DELETE FROM events WHERE id NOT IN "
                "(SELECT id FROM events ORDER BY id DESC LIMIT ?)",
                (int(keep),),
            )
