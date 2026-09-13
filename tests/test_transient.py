"""「临时性故障不消耗重试次数」的回归测试。

背景（真机实测）：NapCat 取下载直链依赖它内部的 fileUUID 映射，而该映射
**只对当前会话里枚举过的文件有效**。映射失效时 `get_group_file_url` 对
**所有**文件都回 ``real fileUUID not found!`` —— 包括之前成功下载过的文件。

当时的处理把它当成普通失败：``max_retries=1`` 时**一次就永久判死**，
用户看到的是"185 个文件全失败、任务不再推进"。

正确行为：识别为临时性故障 → 退回待处理、**不 +1 重试次数** → 稍后自然重试。
"""

from __future__ import annotations

import unittest

from qgb.errors import NapCatError, QgbError, TransientError
from qgb.models import TransferState
from tests.test_pipeline import GROUP, PDF, PipelineCase


class _TransientClient:
    """包一层：让取直链永远抛 TransientError，其余行为转发给真实假客户端。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get_group_file_url(self, *a, **kw):
        raise TransientError("QQ 组件暂时拿不到该文件的下载直链（fileUUID 未就绪）")


class TransientErrorTest(unittest.TestCase):
    def test_is_an_error_subclass(self) -> None:
        self.assertTrue(issubclass(TransientError, QgbError))
        self.assertTrue(issubclass(NapCatError, QgbError))


class PipelineTransientTest(PipelineCase):
    """**回归**：临时故障不消耗 attempts、不落 FAILED。"""

    def _prepare(self):
        pipeline, store, events = self.build(max_retries=1)
        # 把客户端换成"取直链永远临时失败"的版本
        pipeline.client = _TransientClient(pipeline.client)
        self._register(store)
        return pipeline, store, events

    def _register(self, store) -> None:
        from qgb.models import GroupFile

        for fid, name, size in (("f-pdf", PDF, len(self.pdf_bytes)),
                                ("f-xlsx", "数据.xlsx", len(self.xlsx_bytes))):
            gf = GroupFile(group_id=GROUP, file_id=fid, name=name, size=size, busid=102)
            store.upsert_file(gf)
            store.claim(gf)

    def test_attempts_not_consumed(self) -> None:
        pipeline, store, _events = self._prepare()
        pipeline.run_once()

        for fid in ("f-pdf", "f-xlsx"):
            row = store.get_transfer((GROUP, 102, fid))
            self.assertIsNotNone(row)
            self.assertEqual(
                int(row["attempts"]), 0,
                "临时故障不该消耗重试次数 —— 否则 max_retries=1 时一次就永久判死",
            )
            self.assertEqual(
                row["state"], TransferState.DISCOVERED.value,
                "临时故障应退回待处理，等待下次循环",
            )

    def test_not_marked_failed_even_with_one_retry(self) -> None:
        pipeline, store, _events = self._prepare()
        pipeline.run_once()
        pipeline.run_once()
        pipeline.run_once()

        failed = store.by_state(TransferState.FAILED)
        self.assertEqual(failed, [], "临时故障连续三轮都不该落 FAILED")

    def test_emits_actionable_warning(self) -> None:
        pipeline, _store, events = self._prepare()
        pipeline.run_once()
        self.assertTrue(
            any("暂时跳过" in e for e in events),
            f"应给出「暂时跳过、稍后重试」的提示，实际事件：{events[:4]}",
        )
        self.assertTrue(any("稍后自动重试" in e for e in events), "提示里要写清会重试")

    def test_still_advances_through_whole_queue(self) -> None:
        """一批里所有文件都临时失败时，仍要全部走完（不能卡在队首）。"""
        pipeline, store, _events = self._prepare()
        pipeline.run_once()
        rows = {r["file_id"]: int(r["attempts"]) for r in store.list_files()
                if r["file_id"] in ("f-pdf", "f-xlsx")}
        self.assertEqual(set(rows), {"f-pdf", "f-xlsx"}, "两个文件都该被处理过")
        self.assertTrue(all(v == 0 for v in rows.values()), "都不该消耗重试次数")


def load_tests(loader, tests, pattern):  # noqa: ARG001
    suite = unittest.TestSuite()
    suite.addTest(TransientErrorTest("test_is_an_error_subclass"))
    for m in ("test_attempts_not_consumed",
              "test_not_marked_failed_even_with_one_retry",
              "test_emits_actionable_warning",
              "test_still_advances_through_whole_queue"):
        suite.addTest(PipelineTransientTest(m))
    return suite


if __name__ == "__main__":
    unittest.main()
