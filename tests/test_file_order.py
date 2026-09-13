"""手工排序与置顶的回归测试（`StateStore.pending_ordered` 的语义契约）。

用户要的行为是「**完全按我排的顺序**下载」，所以这里把三条排序规则的
优先级钉死，并覆盖两个踩过的坑：

1. ``manual_order`` 默认 0：如果直接 ``ORDER BY manual_order``，
   未排过的行（0）会插到已排序行（10、20…）**中间**。
   必须把"没排过"当作极大值排到最后。
2. 取消置顶**只清 pinned 标记**，不动 ``manual_order`` ——
   文件仍留在手工序列的原位置，而不是跳回队尾。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.models import GroupFile, TransferState
from qgb.store import StateStore


class OrderingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-order-")
        self.store = StateStore(Path(self._tmp.name) / "state.db")
        for i, name in enumerate(("a.pdf", "b.pdf", "c.pdf")):
            gf = GroupFile(group_id="111", file_id=f"f{i}", name=name, size=100, busid=1)
            self.store.upsert_file(gf)
            self.store.claim(gf)

    def tearDown(self) -> None:
        # 先关连接：Windows 上 SQLite 会锁住文件，不关就删不掉目录
        self.store.close()
        self._tmp.cleanup()

    def order(self) -> list[str]:
        return [r["name"] for r in self.store.pending_ordered()]

    # -------------------------------------------------- 基本顺序

    def test_default_is_first_in_first_out(self) -> None:
        self.assertEqual(self.order(), ["a.pdf", "b.pdf", "c.pdf"])

    def test_pin_puts_file_first(self) -> None:
        self.store.set_file_order([("111", 1, "f2")], pinned=True)
        self.assertEqual(self.order(), ["c.pdf", "a.pdf", "b.pdf"])

    def test_manual_order_wins_within_group(self) -> None:
        self.store.set_file_order([("111", 1, "f1"), ("111", 1, "f0")])
        self.assertEqual(self.order(), ["b.pdf", "a.pdf", "c.pdf"])

    def test_pinned_beats_manual_order(self) -> None:
        self.store.set_file_order([("111", 1, "f1"), ("111", 1, "f0")])   # b, a
        self.store.set_file_order([("111", 1, "f2")], pinned=True)        # c 置顶
        self.assertEqual(self.order(), ["c.pdf", "b.pdf", "a.pdf"])

    # -------------------------------------------------- 两个踩过的坑

    def test_unordered_rows_go_last_not_in_the_middle(self) -> None:
        """**回归点**：未手工排过的行（manual_order=0）不能插到已排序行中间。

        直接 ``ORDER BY manual_order`` 时 0 < 10，未排序的行会跑到前面 ——
        实测出现过 ``['b.pdf','c.pdf','a.pdf']`` 这种"中间插队"。
        """
        self.store.set_file_order([("111", 1, "f1"), ("111", 1, "f0")])   # b(10), a(20)
        # c 从未被排序，应排在 a 之后（而不是 b、a 之间）
        self.assertEqual(self.order(), ["b.pdf", "a.pdf", "c.pdf"])

    def test_unpin_keeps_manual_position(self) -> None:
        """**回归点**：取消置顶只清 pinned，不动 manual_order。

        用户取消置顶是"不再抢到最前"，不是"回到队尾"。
        """
        self.store.set_file_order([("111", 1, "f2")], pinned=True)
        self.store.set_file_order([("111", 1, "f1"), ("111", 1, "f0")])   # b(10), a(20)
        self.assertEqual(self.order(), ["c.pdf", "b.pdf", "a.pdf"])

        self.store.set_pinned("111", 1, "f2", False)
        # c 仍有 manual_order=10，与 b 同序，按发现时间在前 → b, c, a
        self.assertEqual(self.order(), ["b.pdf", "c.pdf", "a.pdf"])
        rows = {r["name"]: r for r in self.store.pending_ordered()}
        self.assertEqual(rows["c.pdf"]["pinned"], 0)
        self.assertGreater(int(rows["c.pdf"]["manual_order"]), 0)

    # -------------------------------------------------- 边界

    def test_order_only_affects_pending(self) -> None:
        self.store.mark(("111", 1, "f0"), TransferState.DONE)
        self.store.set_file_order([("111", 1, "f2")], pinned=True)
        self.assertEqual(self.order(), ["c.pdf", "b.pdf"])

    def test_set_file_order_reports_rows_changed(self) -> None:
        self.assertEqual(self.store.set_file_order([("111", 1, "f0")]), 1)
        self.assertEqual(self.store.set_file_order([("111", 1, "nope")]), 0)


class MigrationTest(unittest.TestCase):
    """老库升级：``CREATE TABLE IF NOT EXISTS`` 不会给已存在的表补列。"""

    def test_old_database_gets_new_columns(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory(prefix="qgb-migrate-") as td:
            path = Path(td) / "old.db"
            conn = sqlite3.connect(str(path))
            conn.executescript(
                """
                CREATE TABLE transfers (
                    group_id TEXT NOT NULL, busid INTEGER NOT NULL, file_id TEXT NOT NULL,
                    name TEXT NOT NULL, size INTEGER NOT NULL DEFAULT 0,
                    local_path TEXT NOT NULL DEFAULT '', remote_path TEXT NOT NULL DEFAULT '',
                    sha256 TEXT NOT NULL DEFAULT '', state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    PRIMARY KEY (group_id, busid, file_id));
                INSERT INTO transfers(group_id,busid,file_id,name,state,created_at,updated_at)
                VALUES('111',1,'f1','a.pdf','discovered',1,1);
                """
            )
            conn.commit()
            conn.close()

            store = StateStore(path)                      # 打开即迁移
            cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(transfers)")}
            self.assertIn("manual_order", cols)
            self.assertIn("pinned", cols)
            self.assertEqual(len(store.pending_ordered()), 1, "原有数据不能丢")

            # 迁移后可正常读写新列
            store.set_file_order([("111", 1, "f1")], pinned=True)
            row = store.pending_ordered()[0]
            self.assertEqual(row["pinned"], 1)
            store.close()                                 # 释放文件锁再让 TemporaryDirectory 清理


if __name__ == "__main__":
    unittest.main()
