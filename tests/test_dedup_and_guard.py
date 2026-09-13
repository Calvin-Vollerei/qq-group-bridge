"""四个用户实测问题的回归测试。

1. **「优先执行」永远提示"没有待处理"** —— 列里存的是中文标签，又拿它去查
   ``state_label``（键是 ``discovered``），永远查不到 → 按钮形同虚设。
2. **有任务在跑时又触发扫描** → 同一批文件被重复枚举/登记。
3. **上传过的文件被重复扫描上传** —— QQ 群文件的 ``file_id`` 会随会话变化，
   只按 file_id 去重就漏掉了"同一个文件"。
4. **置顶不够有效** —— 只排到"已排序段"之前还不够，队列里可能还有几百个更早的项。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.models import GroupFile, TransferState
from qgb.store import StateStore


class _StoreCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-dedup-")
        self.store = StateStore(Path(self._tmp.name) / "state.db")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()


class UploadedByFingerprintTest(_StoreCase):
    """**回归**：file_id 会变，去重必须能按 (群, 文件名, 大小) 命中。"""

    def test_same_name_size_is_recognised(self) -> None:
        gf = GroupFile(group_id="111", file_id="old-id", name="报告.pdf", size=1234, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.store.mark(gf.key, TransferState.DONE)

        self.assertTrue(
            self.store.is_uploaded_like("111", "报告.pdf", 1234),
            "同一个文件换了 file_id 后没被识别 → 会被重复下载上传",
        )

    def test_different_size_is_not_matched(self) -> None:
        gf = GroupFile(group_id="111", file_id="x", name="报告.pdf", size=1234, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.store.mark(gf.key, TransferState.DONE)

        self.assertFalse(self.store.is_uploaded_like("111", "报告.pdf", 999),
                         "大小不同应视为不同文件")

    def test_different_group_is_not_matched(self) -> None:
        gf = GroupFile(group_id="111", file_id="x", name="报告.pdf", size=1234, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.store.mark(gf.key, TransferState.DONE)

        self.assertFalse(self.store.is_uploaded_like("222", "报告.pdf", 1234),
                         "不同群各自要搬一份")

    def test_pending_is_not_treated_as_uploaded(self) -> None:
        gf = GroupFile(group_id="111", file_id="x", name="a.pdf", size=10, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.assertFalse(self.store.is_uploaded_like("111", "a.pdf", 10))

    def test_filtered_is_not_treated_as_uploaded(self) -> None:
        """**回归**：被过滤的文件不算"搬过了"。

        早先把 SKIPPED 也算作已搬，导致改了过滤规则后无法重新纳入，
        而且让"过滤过的文件每轮重新标记"这条既有用例挂掉。
        """
        gf = GroupFile(group_id="111", file_id="x", name="a.pdf", size=10, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.store.mark(gf.key, TransferState.FILTERED_OUT)
        self.assertFalse(self.store.is_uploaded_like("111", "a.pdf", 10),
                         "过滤掉的不该被当成已搬过 —— 否则改规则后无法重新纳入")

    def test_skipped_is_not_treated_as_uploaded(self) -> None:
        gf = GroupFile(group_id="111", file_id="y", name="b.pdf", size=20, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.store.mark(gf.key, TransferState.SKIPPED)
        self.assertFalse(self.store.is_uploaded_like("111", "b.pdf", 20))

    def test_failed_is_not_treated_as_uploaded(self) -> None:
        gf = GroupFile(group_id="111", file_id="x", name="a.pdf", size=10, busid=1)
        self.store.upsert_file(gf)
        self.store.claim(gf)
        self.store.mark(gf.key, TransferState.FAILED)
        self.assertFalse(self.store.is_uploaded_like("111", "a.pdf", 10),
                         "失败过的不算搬过，应允许重试")


class PinnedFirstTest(_StoreCase):
    """**回归**：置顶项必须排在**最前面**，而不是"已排序段之前"。"""

    def test_pinned_beats_earlier_unordered_items(self) -> None:
        for i in range(5):
            gf = GroupFile(group_id="111", file_id=f"f{i}", name=f"{i}.pdf", size=10, busid=1)
            self.store.upsert_file(gf)
            self.store.claim(gf)

        # 把最后一个置顶：它必须排到第 1 位（而不是"第 4 位之前"）
        self.store.set_file_order([("111", 1, "f4")], pinned=True)
        order = [r["file_id"] for r in self.store.pending_ordered()]
        self.assertEqual(order[0], "f4", f"置顶项没到队首：{order}")

    def test_pinned_survives_manual_order(self) -> None:
        for i in range(4):
            gf = GroupFile(group_id="111", file_id=f"f{i}", name=f"{i}.pdf", size=10, busid=1)
            self.store.upsert_file(gf)
            self.store.claim(gf)

        self.store.set_file_order([("111", 1, "f3"), ("111", 1, "f2")])   # 手工：f3, f2
        self.store.set_file_order([("111", 1, "f1")], pinned=True)        # f1 置顶
        order = [r["file_id"] for r in self.store.pending_ordered()]
        self.assertEqual(order[0], "f1")
        self.assertEqual(order[1:3], ["f3", "f2"], "手工顺序应保留在置顶之后")


class RunOnceGuardTest(unittest.TestCase):
    """**回归**：一轮未结束时再次调用 run_once 不应重入。"""

    def test_second_call_is_refused_while_busy(self) -> None:
        import threading
        import time
        from unittest import mock

        from qgb.pipeline import Pipeline, PipelineStats

        pipe = Pipeline.__new__(Pipeline)          # 只测 busy/重入逻辑，不建整个流水线
        pipe._busy = threading.Event()
        pipe.stats = PipelineStats()
        entered = threading.Event()

        def slow():
            entered.set()
            time.sleep(0.4)
            return PipelineStats(uploaded=1)

        pipe._run_once_locked = slow              # type: ignore[method-assign]
        result: list[PipelineStats] = []

        t = threading.Thread(target=lambda: result.append(pipe.run_once()))
        t.start()
        entered.wait(2.0)
        self.assertTrue(pipe.busy, "执行期间 busy 应为 True")

        second = pipe.run_once()                  # 应当被拒绝
        self.assertEqual(second.uploaded, 0, "重入调用不该真的跑一轮")
        t.join(3.0)

        self.assertFalse(pipe.busy, "结束后 busy 应复位")
        self.assertEqual(result[0].uploaded, 1)


if __name__ == "__main__":
    unittest.main()
