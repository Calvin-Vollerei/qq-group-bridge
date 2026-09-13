"""「fileUUID 未就绪」的真正根因与修复的回归测试。

真机探针结论（scripts/probe-stale-pending.py）：

    群            待处理   当前列表  file_id 命中  文件名命中  对列表内文件取直链
    922161761     3065     999      0 (0%)       420 (80%)   ✅ 成功
    838090744     2544     297      0 (0%)       165 (65%)   ✅ 成功
    1042146604     845     103      0 (0%)        52 (50%)   ✅ 成功
    1072994777     384      82      0 (0%)        53 (100%)  ✅ 成功

即：**库里存的 file_id 全部失效（0% 命中），但文件本身还在群里（按名命中 50-100%）**，
而用当前列表里的 id 取直链是成功的。

根因：``transfers.file_id`` 是登记那一刻固定的，而群文件的 file_id 会变；
扫描只刷新了 ``files`` 表，``transfers`` 里仍是旧值。

修法：下载前用 (文件名, 大小) 从当前群列表解析出有效 id 并写回。
"""

from __future__ import annotations

import unittest

from qgb.errors import TransientError
from qgb.models import GroupFile, TransferState
from tests.test_pipeline import GROUP, PDF, XLSX, PipelineCase


class LiveFileIdTest(PipelineCase):
    """**回归**：file_id 失效时，必须能按名字+大小解析出当前值并成功下载。

    构造：队列里放**旧 id** 的待处理项，群列表里是**新 id**（且直链只挂在新 id 上）。
    只跑一轮 —— 一轮内既扫描又搬运，正好覆盖真实流程。
    """

    def _prepare(self) -> tuple:
        fresh_pdf, fresh_xlsx = "fresh-pdf-id", "fresh-xlsx-id"
        files = [
            GroupFile(group_id=GROUP, file_id=fresh_pdf, name=PDF,
                      size=len(self.pdf_bytes), busid=102),
            GroupFile(group_id=GROUP, file_id=fresh_xlsx, name=XLSX,
                      size=len(self.xlsx_bytes), busid=102),
        ]
        # 直链只挂在**新** id 上：用旧 id 取必然失败
        urls = {
            (GROUP, fresh_pdf): self.server.url_for(PDF),
            (GROUP, fresh_xlsx): self.server.url_for(XLSX),
        }
        pipeline, store, events = self.build(files=files, urls=urls)

        # 队列里放"旧 id"的历史记录（同名同大小）
        for old_id, name, size in (("old-pdf-id", PDF, len(self.pdf_bytes)),
                                   ("old-xlsx-id", XLSX, len(self.xlsx_bytes))):
            gf = GroupFile(group_id=GROUP, file_id=old_id, name=name, size=size, busid=102)
            store.upsert_file(gf)
            store.claim(gf)
        return pipeline, store, events, fresh_pdf

    def test_download_succeeds_after_resolving_fresh_id(self) -> None:
        pipeline, _store, _events, _fp = self._prepare()

        stats = pipeline.run_once()

        self.assertGreaterEqual(stats.uploaded, 2,
                                "file_id 失效后没有解析出当前值 → 下载失败")
        self.assertTrue((self.netdisk / PDF).is_file(), "PDF 没被搬走")

    def test_old_id_row_is_gone(self) -> None:
        """旧 id 的行必须被清掉/迁移，否则下次还会用旧 id 去取直链。"""
        pipeline, store, _events, _fp = self._prepare()
        pipeline.run_once()

        self.assertIsNone(store.get_transfer((GROUP, 102, "old-pdf-id")),
                          "旧 id 的行仍在 → 下一轮又会用旧 id 取直链而失败")

    def test_no_duplicate_upload_in_second_cycle(self) -> None:
        """解析后仍要走去重：第二轮不该再搬一遍。"""
        pipeline, _store, _events, _fp = self._prepare()
        first = pipeline.run_once()
        second = pipeline.run_once()
        self.assertGreaterEqual(first.uploaded, 2)
        self.assertEqual(second.uploaded, 0, "第二轮重复搬运了")


class MissingFromGroupTest(PipelineCase):
    """群列表里确实没有该文件时：按临时故障处理（不永久判死）。"""

    def test_absent_file_is_transient_not_failed(self) -> None:
        gone = GroupFile(group_id=GROUP, file_id="gone-id", name="已删除.pdf",
                         size=1234, busid=102)
        pipeline, store, events = self.build(files=[], urls={}, max_retries=1)
        store.upsert_file(gone)
        store.claim(gone)

        pipeline.run_once()

        rec = store.get_transfer((GROUP, 102, "gone-id"))
        self.assertIsNotNone(rec)
        self.assertEqual(rec["state"], TransferState.DISCOVERED.value,
                         "不该因为一次核对失败就永久判死")
        self.assertEqual(int(rec["attempts"]), 0, "临时故障不消耗重试次数")
        self.assertTrue(any("找不到" in e or "未取全" in e for e in events),
                        f"应给出可读原因，实际：{events[:3]}")


def load_tests(loader, tests, pattern):  # noqa: ARG001
    suite = unittest.TestSuite()
    for cls_name, names in (
        ("LiveFileIdTest", ("test_download_succeeds_after_resolving_fresh_id",
                            "test_old_id_row_is_gone",
                            "test_no_duplicate_upload_in_second_cycle")),
        ("MissingFromGroupTest", ("test_absent_file_is_transient_not_failed",)),
    ):
        cls = globals()[cls_name]
        for n in names:
            suite.addTest(cls(n))
    return suite


if __name__ == "__main__":
    unittest.main()
