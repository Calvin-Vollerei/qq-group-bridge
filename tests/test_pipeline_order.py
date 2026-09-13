"""流水线的下载顺序与「失败即跳下一个」的回归测试。

两件事都来自用户实测反馈：

1. **一个下载失败会卡住整批** —— ``_handle_failure`` 把文件 mark 回
   ``DISCOVERED``，而 ``_drain_pending`` 遍历的是循环开始时取的**快照**，
   于是队首那个坏文件被立刻重新处理：连续占满重试次数，后面的文件一直排不上。
   正确行为是**同一批内先跳到下一个文件**，下次循环再回来重试它。

2. **顺序即下载顺序** —— 界面里置顶/上移下移的结果，必须真的是流水线消费的顺序。
"""

from __future__ import annotations

import unittest

from qgb.models import GroupFile, TransferState
from tests.test_pipeline import GROUP, PDF, XLSX, PipelineCase

BAD = "坏文件.pdf"


class FailureDoesNotBlockOthersTest(PipelineCase):
    """**回归**：一个文件失败，不能让后面的文件排队等待。"""

    def _build_with_bad_first(self, *, max_retries: int = 3):
        files = [
            GroupFile(group_id=GROUP, file_id="f-bad", name=BAD,
                      size=len(self.pdf_bytes), busid=102),
            GroupFile(group_id=GROUP, file_id="f-xlsx", name=XLSX,
                      size=len(self.xlsx_bytes), busid=102),
            GroupFile(group_id=GROUP, file_id="f-pdf", name=PDF,
                      size=len(self.pdf_bytes), busid=102),
        ]
        urls = {          # 故意不给 f-bad 登记直链 → 它必然下载失败
            (GROUP, "f-xlsx"): self.server.url_for(XLSX),
            (GROUP, "f-pdf"): self.server.url_for(PDF),
        }
        return self.build(files=files, urls=urls, max_retries=max_retries)

    def test_other_files_still_get_processed(self) -> None:
        """坏文件排第一时，后面的两个文件在同一轮里也要被搬完。"""
        pipeline, store, _events = self._build_with_bad_first()
        stats = pipeline.run_once()

        self.assertEqual(stats.uploaded, 2, "后面的文件被坏文件挡住了")
        self.assertTrue((self.netdisk / XLSX).is_file())
        self.assertTrue((self.netdisk / PDF).is_file())

    def test_failing_file_is_attempted_once_per_cycle(self) -> None:
        """坏文件在**同一批**里只该被试一次，不能连续吃满重试次数。"""
        pipeline, store, _events = self._build_with_bad_first(max_retries=3)
        pipeline.run_once()

        row = store.get_transfer((GROUP, 102, "f-bad"))
        self.assertIsNotNone(row)
        self.assertEqual(
            int(row["attempts"]), 1,
            "同一批内重复尝试了同一个坏文件 —— 会挡住后面的文件",
        )
        self.assertEqual(row["state"], TransferState.DISCOVERED.value,
                         "未达重试上限时应退回待处理，等待下次循环")

    def test_failed_after_max_retries_moves_to_failed_state(self) -> None:
        """达到重试上限后应落到 FAILED，不再占着队列。"""
        pipeline, store, _events = self._build_with_bad_first(max_retries=1)
        pipeline.run_once()

        row = store.get_transfer((GROUP, 102, "f-bad"))
        self.assertEqual(row["state"], TransferState.FAILED.value)

    def test_retry_happens_on_next_cycle(self) -> None:
        """下一轮（而非同一轮）才会重试坏文件 —— 这就是"稍后重试"。"""
        pipeline, store, _events = self._build_with_bad_first(max_retries=3)
        pipeline.run_once()
        after_first = int(store.get_transfer((GROUP, 102, "f-bad"))["attempts"])

        pipeline.run_once()
        after_second = int(store.get_transfer((GROUP, 102, "f-bad"))["attempts"])
        self.assertEqual(after_second, after_first + 1, "第二轮应重试一次")

        # 已搬完的文件不该被重复搬运
        self.assertTrue(store.is_uploaded((GROUP, 102, "f-pdf")))


class PinnedGoesFirstTest(PipelineCase):
    """**回归**：置顶的文件必须最先被搬运 —— 顺序即下载顺序。"""

    def _register(self, store) -> None:
        """把三个文件登记成待处理 —— 只有登记过的行才能设顺序。

        ⚠️ 踩过的坑：直接在 ``run_once()`` 之前调 ``set_file_order`` 是**无效**的，
        因为那一行 transfers 还不存在（UPDATE 匹配不到任何行，且不报错）。
        """
        for fid, name, size in (
            ("f-pdf", PDF, len(self.pdf_bytes)),
            ("f-xlsx", XLSX, len(self.xlsx_bytes)),
            ("f-docx", "会议记录.docx", len(self.docx_bytes)),
        ):
            gf = GroupFile(group_id=GROUP, file_id=fid, name=name, size=size, busid=102)
            store.upsert_file(gf)
            store.claim(gf)

    def test_pinned_file_is_downloaded_first(self) -> None:
        pipeline, store, _events = self.build()
        self._register(store)

        # 把 xlsx 置顶（pdf 原本在前）
        self.assertGreater(store.set_file_order([(GROUP, 102, "f-xlsx")], pinned=True), 0,
                           "置顶没有写入（行不存在？）")
        order = [r["file_id"] for r in store.pending_ordered()]
        self.assertEqual(order[0], "f-xlsx", "置顶项没有排到队首")

        pipeline.run_once()

        # 语义说明（v1.9 起）：置顶/优先项**独占第一批**，所以首轮只搬它；
        # 其余文件在下一轮继续。这样用户点完「优先执行」能立刻在日志里看到
        # 自己那批，而不是混在 200 个普通项里。
        self.assertEqual(store.get_transfer((GROUP, 102, "f-xlsx"))["state"],
                         TransferState.DONE.value, "置顶的文件没有被优先搬运")

        pipeline.run_once()          # 第二轮处理其余
        self.assertEqual(store.get_transfer((GROUP, 102, "f-pdf"))["state"],
                         TransferState.DONE.value, "其余文件在下一轮也该被搬走")

    def test_manual_order_is_respected_for_unpinned(self) -> None:
        _pipeline, store, _events = self.build()
        self._register(store)
        # 手工顺序：xlsx 排到 pdf 前面
        store.set_file_order([(GROUP, 102, "f-xlsx"), (GROUP, 102, "f-pdf")])
        order = [r["name"] for r in store.pending_ordered()]
        self.assertEqual(order[0], XLSX, "手工顺序没有生效")
        self.assertEqual(order[1], PDF)


def load_tests(loader, tests, pattern):  # noqa: ARG001
    """只跑本文件新增的用例。

    这两个类继承了 ``PipelineCase`` 以获得它的夹具（RangeFileServer、store、
    tmp 目录、tearDown 顺序），但**不要**把基类的全部用例再跑一遍 ——
    那会让测试列表里出现大量重复名字，也让失败定位变难。
    """
    ours = {
        "FailureDoesNotBlockOthersTest": [
            "test_other_files_still_get_processed",
            "test_failing_file_is_attempted_once_per_cycle",
            "test_failed_after_max_retries_moves_to_failed_state",
            "test_retry_happens_on_next_cycle",
        ],
        "PinnedGoesFirstTest": [
            "test_pinned_file_is_downloaded_first",
            "test_manual_order_is_respected_for_unpinned",
        ],
    }
    suite = unittest.TestSuite()
    for cls_name, methods in ours.items():
        cls = globals()[cls_name]
        for m in methods:
            suite.addTest(cls(m))
    return suite


if __name__ == "__main__":
    unittest.main()
