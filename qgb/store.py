"""SQLite 状态库：去重主键、任务状态、运行期 KV。

为什么用 SQLite 而不是 ``config.json``：
  * 去重写入是**高频事务**，JSON 全量重写既慢又容易损坏
  * 崩溃后可安全恢复（WAL 模式）
  * 天然支持按状态查询「还有哪些没搬完」
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from .errors import QgbError
from .models import GroupFile, TransferState

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
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    PRIMARY KEY (group_id, busid, file_id)
);

CREATE INDEX IF NOT EXISTS idx_transfers_state   ON transfers(state);
CREATE INDEX IF NOT EXISTS idx_transfers_updated ON transfers(updated_at);

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
            self._conn.commit()
            self._conn.execute(
                "INSERT OR IGNORE INTO kv(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(f"无法打开状态库：{exc}") from exc

    # -------------------------------------------------- 基础设施

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
