"""「清空记录并重新发现」与「临时故障冷却」的回归测试。

两个都来自用户实测反馈：

1. **「哪来那么多文件，你肯定是把历史全部叠加起来了」** —— 用户判断正确。
   ``transfers`` 是累计的：一次全量扫描把群里所有文件登记成「待处理」，
   之后即使文件已删除也会留着。实测用户库里积了 48,035 条，最早的是前一天留下的。
   → 新增 ``reset_discovery``。**关键风险**：清空不能把已搬完的记录也删掉，
   否则所有文件会被重新下载上传一遍。

2. **「下载依然没有开始」** —— 上一版把 ``real fileUUID not found`` 归为
   临时故障、**不消耗重试次数**，副作用是这些旧记录**下一轮立刻又排队首**，
   把尝试名额全吃掉，新文件永远轮不到。→ 新增冷却 ``retry_at``。
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from qgb.models import GroupFile, TransferState
from qgb.store import StateStore


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-reset-")
        self.store = StateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _file(self, fid: str, name: str = "") -> GroupFile:
        gf = GroupFile(group_id="111", file_id=fid, name=name or f"{fid}.pdf",
                       size=100, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        return gf


class ResetDiscoveryTest(_StoreCase):
    def test_clears_pending_and_filtered_and_failed(self) -> None:
        self._file("a")
        self._file("b")
        self._file("c")
        self.store.mark(("111", 1, "b"), TransferState.FILTERED_OUT)
        self.store.mark(("111", 1, "c"), TransferState.FAILED)

        result = self.store.reset_discovery()
        self.assertEqual(result["removed"], 3)
        self.assertEqual(self.store.pending_ordered(), [])
        self.assertEqual(self.store.by_state(TransferState.FAILED), [])

    def test_keeps_done_records_for_dedup(self) -> None:
        """**关键**：已搬完的记录必须保留，否则会全部重新下载上传。"""
        self._file("done-1")
        self.store.mark(("111", 1, "done-1"), TransferState.DONE)
        self._file("pending-1")

        self.store.reset_discovery()

        self.assertTrue(self.store.is_settled(("111", 1, "done-1")),
                        "已完成的记录被删掉了 —— 会导致重复搬运")
        self.assertTrue(self.store.is_uploaded(("111", 1, "done-1")))
        self.assertEqual(len(self.store.pending_ordered()), 0)

    def test_seen_files_survive(self) -> None:
        """files 表（已见）也保留，用于判断"这个文件我见过"。"""
        self._file("x")
        self.store.reset_discovery()
        self.assertGreater(self.store.seen_count(), 0)

    def test_result_reports_before_counts(self) -> None:
        self._file("a")
        self._file("b")
        self.store.mark(("111", 1, "b"), TransferState.DONE)
        result = self.store.reset_discovery()
        self.assertEqual(result["removed"], 1, "只该清掉那 1 条待处理的")
        # before_* 是"清空前各状态的条数"：b 已是 done，所以待处理只剩 1 条
        self.assertEqual(result["before_discovered"], 1)
        self.assertEqual(result["before_done"], 1)


class CooldownTest(_StoreCase):
    """**回归**：临时故障不能下一轮又霸占队首。"""

    def test_cooling_item_is_skipped(self) -> None:
        self._file("bad")
        self._file("good")
        self.store.mark(("111", 1, "bad"), TransferState.DISCOVERED,
                        error="临时", retry_after=60)

        order = [r["file_id"] for r in self.store.pending_ordered()]
        self.assertNotIn("bad", order, "冷却中的项仍在队列里 —— 会挡住后面的文件")
        self.assertIn("good", order)

    def test_item_returns_after_cooldown(self) -> None:
        self._file("bad")
        self.store.mark(("111", 1, "bad"), TransferState.DISCOVERED, retry_after=0.05)
        self.assertEqual(self.store.pending_ordered(), [])
        time.sleep(0.15)
        self.assertEqual([r["file_id"] for r in self.store.pending_ordered()], ["bad"])

    def test_zero_cooldown_does_not_block(self) -> None:
        self._file("a")
        self.store.mark(("111", 1, "a"), TransferState.DISCOVERED, retry_after=0)
        self.assertEqual([r["file_id"] for r in self.store.pending_ordered()], ["a"])

    def test_default_is_no_cooldown(self) -> None:
        """普通 mark（不传 retry_after）不该受冷却影响。"""
        self._file("a")
        self.store.mark(("111", 1, "a"), TransferState.DISCOVERED)
        self.assertEqual(len(self.store.pending_ordered()), 1)


if __name__ == "__main__":
    unittest.main()
