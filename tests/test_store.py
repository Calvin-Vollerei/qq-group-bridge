"""状态库（去重 / 状态机）测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.models import GroupFile, TransferState
from qgb.store import StateStore


def gf(file_id: str = "f1", name: str = "报告.pdf", size: int = 100, group: str = "123456") -> GroupFile:
    return GroupFile(group_id=group, file_id=file_id, name=name, size=size, busid=102)


class TestStateStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-store-")
        self.tmp = Path(self._tmp.name)
        self.store = StateStore(self.tmp / "state.db")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    # -------------------------------------------------- 台账

    def test_upsert_file_is_idempotent(self) -> None:
        f = gf()
        self.store.upsert_file(f)
        self.store.upsert_file(f)
        self.assertEqual(self.store.seen_count(), 1)

    def test_upsert_refreshes_metadata(self) -> None:
        self.store.upsert_file(gf(size=100))
        self.store.upsert_file(gf(size=200))
        self.assertEqual(self.store.seen_count(), 1)

    # -------------------------------------------------- 认领与去重

    def test_claim_conflict_returns_false(self) -> None:
        f = gf()
        self.assertTrue(self.store.claim(f))
        self.assertFalse(self.store.claim(f), "同一文件二次认领必须失败（去重核心）")

    def test_same_file_id_different_busid_is_distinct(self) -> None:
        a = GroupFile(group_id="1", file_id="same", name="a.pdf", busid=102)
        b = GroupFile(group_id="1", file_id="same", name="b.pdf", busid=104)
        self.assertTrue(self.store.claim(a))
        self.assertTrue(self.store.claim(b))
        self.assertEqual(self.store.stats()[TransferState.DISCOVERED.value], 2)

    def test_dedup_key_includes_group(self) -> None:
        a = gf(group="111")
        b = gf(group="222")
        self.assertTrue(self.store.claim(a))
        self.assertTrue(self.store.claim(b))

    def test_is_settled_and_is_uploaded(self) -> None:
        f = gf()
        self.store.claim(f)
        self.assertFalse(self.store.is_settled(f.key))

        self.store.mark(f.key, TransferState.UPLOADED, remote_path="/x/a.pdf")
        self.assertTrue(self.store.is_settled(f.key))
        self.assertTrue(self.store.is_uploaded(f.key))

        self.store.mark(f.key, TransferState.DONE)
        self.assertTrue(self.store.is_settled(f.key))

    def test_filtered_out_counts_as_settled(self) -> None:
        f = gf()
        self.store.claim(f)
        self.store.mark(f.key, TransferState.FILTERED_OUT, error="不匹配")
        self.assertTrue(self.store.is_settled(f.key))
        self.assertFalse(self.store.is_uploaded(f.key))

    # -------------------------------------------------- 状态推进

    def test_mark_updates_fields_and_attempts(self) -> None:
        f = gf()
        self.store.claim(f)
        self.store.mark(f.key, TransferState.FAILED, error="网络错误", bump_attempts=True)
        rec = self.store.get_transfer(f.key)
        self.assertEqual(rec["state"], TransferState.FAILED.value)
        self.assertEqual(rec["error"], "网络错误")
        self.assertEqual(rec["attempts"], 1)

    def test_pending_and_by_state(self) -> None:
        a, b = gf("a"), gf("b")
        self.store.claim(a)
        self.store.claim(b)
        self.store.mark(b.key, TransferState.UPLOADED)
        pending = self.store.pending()
        self.assertEqual([r["file_id"] for r in pending], ["a"])
        self.assertEqual(len(self.store.by_state(TransferState.UPLOADED)), 1)

    def test_reset_stale_returns_interrupted_to_pending(self) -> None:
        f = gf()
        self.store.claim(f)
        for state in (TransferState.DOWNLOADING, TransferState.DOWNLOADED, TransferState.UPLOADING):
            self.store.mark(f.key, state)
            n = self.store.reset_stale(
                (TransferState.DOWNLOADING, TransferState.DOWNLOADED, TransferState.UPLOADING)
            )
            self.assertEqual(n, 1)
            self.assertEqual(self.store.get_transfer(f.key)["state"], TransferState.DISCOVERED.value)

    def test_reset_stale_is_noop_when_clean(self) -> None:
        f = gf()
        self.store.claim(f)
        n = self.store.reset_stale((TransferState.DOWNLOADING,))
        self.assertEqual(n, 0)

    def test_stats_covers_all_states(self) -> None:
        stats = self.store.stats()
        for state in TransferState:
            self.assertIn(state.value, stats)

    def test_recent_orders_by_update(self) -> None:
        a, b = gf("a"), gf("b")
        self.store.claim(a)
        self.store.claim(b)
        self.store.mark(a.key, TransferState.UPLOADED)
        rows = self.store.recent()
        self.assertEqual(len(rows), 2)

    # -------------------------------------------------- KV / 事件

    def test_kv_roundtrip(self) -> None:
        self.store.set_kv("last_poll", "1700000000")
        self.assertEqual(self.store.get_kv("last_poll"), "1700000000")
        self.store.set_kv("last_poll", "1700000001")
        self.assertEqual(self.store.get_kv("last_poll"), "1700000001")
        self.assertIsNone(self.store.get_kv("missing"))

    def test_events_and_prune(self) -> None:
        for i in range(10):
            self.store.add_event("info", f"事件{i}", group_id="1", name="a.pdf")
        self.assertEqual(len(self.store.events(limit=5)), 5)
        self.store.prune_events(keep=3)
        self.assertEqual(len(self.store.events(limit=100)), 3)

    # -------------------------------------------------- 持久化

    def test_survives_reopen(self) -> None:
        f = gf()
        self.store.claim(f)
        self.store.mark(f.key, TransferState.DONE)
        self.store.close()

        reopened = StateStore(self.tmp / "state.db")
        try:
            self.assertTrue(reopened.is_uploaded(f.key))
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
