"""流水线端到端测试（完全离线，无真实账号）。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.dev.fakes import RangeFileServer, make_config, make_secrets
from qgb.models import GroupFile, TransferState
from qgb.napcat.client import FakeOneBotClient
from qgb.pipeline import Pipeline
from qgb.store import StateStore
from qgb.uploaders.local import LocalUploader

GROUP = "123456789"
PDF = "季度报告_v3.pdf"
XLSX = "数据_2026Q1.xlsx"
DOCX = "会议记录.docx"


class PipelineCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-pipe-")
        self.tmp = Path(self._tmp.name)
        self._stores: list[StateStore] = []
        self._pipelines: list[Pipeline] = []

        self.src = self.tmp / "src"
        self.src.mkdir(parents=True, exist_ok=True)
        self.pdf_bytes = b"PDF\x00" * 40000           # ~160KB
        self.xlsx_bytes = b"XLSX\x00" * 12000         # ~60KB
        self.docx_bytes = b"DOCX\x00" * 8000

        (self.src / PDF).write_bytes(self.pdf_bytes)
        (self.src / XLSX).write_bytes(self.xlsx_bytes)
        (self.src / DOCX).write_bytes(self.docx_bytes)

        self.server = RangeFileServer(
            {PDF: self.src / PDF, XLSX: self.src / XLSX, DOCX: self.src / DOCX}
        ).start()
        self.addCleanup(self.server.stop)

    def tearDown(self) -> None:
        # 顺序很重要：先停线程、再关数据库，最后才删目录，
        # 否则 Windows 上会因文件占用导致清理失败。
        for pipeline in self._pipelines:
            try:
                pipeline.stop(timeout=5)
            except Exception:
                pass
        for store in self._stores:
            try:
                store.close()
            except Exception:
                pass
        self._tmp.cleanup()

    # -------------------------------------------------- 组装

    def build(
        self,
        *,
        include=(r".*\.(pdf|xlsx)$",),
        files=None,
        online=True,
        urls=None,
        max_retries=2,
        keep_local_days=0,
        **cfg_kw,
    ) -> tuple[Pipeline, StateStore, list[str]]:
        if files is None:
            files = [
                GroupFile(group_id=GROUP, file_id="f-pdf", name=PDF,
                          size=len(self.pdf_bytes), busid=102),
                GroupFile(group_id=GROUP, file_id="f-xlsx", name=XLSX,
                          size=len(self.xlsx_bytes), busid=102),
                GroupFile(group_id=GROUP, file_id="f-docx", name=DOCX,
                          size=len(self.docx_bytes), busid=102),
            ]

        if urls is None:
            urls = {
                (GROUP, "f-pdf"): self.server.url_for(PDF),
                (GROUP, "f-xlsx"): self.server.url_for(XLSX),
                (GROUP, "f-docx"): self.server.url_for(DOCX),
            }

        client = FakeOneBotClient(files_by_group={GROUP: list(files)}, urls=urls, online=online)
        self.client = client

        cfg = make_config(self.tmp, groups=[GROUP], include=list(include), **cfg_kw)
        cfg.monitor.max_retries = max_retries
        cfg.monitor.keep_local_days = keep_local_days

        secrets = make_secrets(self.tmp)
        store = StateStore(self.tmp / "state.db")
        self._stores.append(store)

        events: list[str] = []
        pipeline = Pipeline(
            cfg, store, secrets, client, LocalUploader(cfg.upload, secrets),
            on_event=lambda e: events.append(f"{e.kind}|{e.level}|{e.message}"),
        )
        self._pipelines.append(pipeline)
        # 本地适配器不拼 remote_root：路径就是 <local_root>/<群号>/...
        self.netdisk = Path(cfg.upload.local_root) / GROUP
        return pipeline, store, events

    # -------------------------------------------------- 正常链路

    def test_end_to_end_uploads_matching_files(self) -> None:
        pipeline, store, _ = self.build()
        stats = pipeline.run_once()

        self.assertEqual(stats.uploaded, 2)
        self.assertTrue((self.netdisk / PDF).is_file())
        self.assertTrue((self.netdisk / XLSX).is_file())
        self.assertEqual((self.netdisk / PDF).read_bytes(), self.pdf_bytes)
        self.assertEqual((self.netdisk / XLSX).read_bytes(), self.xlsx_bytes)

    def test_non_matching_file_is_filtered(self) -> None:
        pipeline, store, events = self.build()
        pipeline.run_once()

        self.assertFalse((self.netdisk / DOCX).exists())
        rec = store.get_transfer((GROUP, 102, "f-docx"))
        self.assertEqual(rec["state"], TransferState.FILTERED_OUT.value)
        self.assertIn("包含规则", rec["error"])
        self.assertTrue(any("发现新文件" in e for e in events))

    def test_state_machine_reaches_done_with_hash(self) -> None:
        import hashlib

        pipeline, store, _ = self.build()
        pipeline.run_once()

        rec = store.get_transfer((GROUP, 102, "f-pdf"))
        self.assertEqual(rec["state"], TransferState.DONE.value)
        self.assertEqual(rec["sha256"], hashlib.sha256(self.pdf_bytes).hexdigest())
        self.assertTrue(rec["remote_path"].endswith(PDF))

    def test_temp_files_are_cleaned(self) -> None:
        pipeline, _, _ = self.build()
        pipeline.run_once()
        leftovers = [p for p in Path(pipeline.cfg.temp_dir).iterdir()]
        self.assertEqual(leftovers, [], f"临时目录未清空：{leftovers}")

    def test_keep_local_days_preserves_copy(self) -> None:
        pipeline, _, _ = self.build(keep_local_days=7)
        pipeline.run_once()
        leftovers = [p for p in Path(pipeline.cfg.temp_dir).iterdir()]
        self.assertTrue(leftovers, "设置了保留期就不应删除本地副本")

    # -------------------------------------------------- 去重

    def test_second_cycle_is_deduped(self) -> None:
        pipeline, store, _ = self.build()
        first = pipeline.run_once()
        second = pipeline.run_once()

        self.assertEqual(first.uploaded, 2)
        self.assertEqual(second.uploaded, 0)
        self.assertEqual(second.discovered, 0)

    def test_settled_filtered_file_not_recounted(self) -> None:
        pipeline, _, _ = self.build()
        pipeline.run_once()
        stats = pipeline.run_once()
        self.assertEqual(stats.filtered, 0, "已处理过的被过滤文件不应重复计数")

    # -------------------------------------------------- 失败与重试

    def test_missing_url_fails_after_retries(self) -> None:
        pipeline, store, events = self.build(
            files=[GroupFile(group_id=GROUP, file_id="f-pdf", name=PDF,
                             size=len(self.pdf_bytes), busid=102)],
            urls={},           # 故意不给直链
            max_retries=2,
        )

        pipeline.run_once()
        rec = store.get_transfer((GROUP, 102, "f-pdf"))
        self.assertEqual(rec["state"], TransferState.DISCOVERED.value,
                         "第一次失败应保持待重试")
        self.assertEqual(rec["attempts"], 1)

        pipeline.run_once()
        rec = store.get_transfer((GROUP, 102, "f-pdf"))
        self.assertEqual(rec["state"], TransferState.FAILED.value)
        self.assertEqual(rec["attempts"], 2)
        self.assertTrue(any("失败" in e for e in events))

    def test_requeue_failed_allows_manual_retry(self) -> None:
        pipeline, store, _ = self.build(
            files=[GroupFile(group_id=GROUP, file_id="f-pdf", name=PDF,
                             size=len(self.pdf_bytes), busid=102)],
            urls={},
            max_retries=1,
        )
        pipeline.run_once()
        self.assertEqual(store.get_transfer((GROUP, 102, "f-pdf"))["state"],
                         TransferState.FAILED.value)

        n = store.requeue()
        self.assertEqual(n, 1)
        rec = store.get_transfer((GROUP, 102, "f-pdf"))
        self.assertEqual(rec["state"], TransferState.DISCOVERED.value)
        self.assertEqual(rec["attempts"], 0)

    # -------------------------------------------------- 登录与暂停

    def test_offline_login_pauses_and_does_not_poll(self) -> None:
        pipeline, store, events = self.build(online=False)
        stats = pipeline.run_once()

        self.assertTrue(pipeline.paused)
        self.assertEqual(stats.discovered, 0)
        self.assertEqual(stats.uploaded, 0)
        self.assertTrue(any("未登录" in e for e in events))

    def test_resume_after_login(self) -> None:
        pipeline, store, _ = self.build(online=False)
        pipeline.run_once()
        self.assertTrue(pipeline.paused)

        # 模拟「扫码后恢复登录」
        self.client.online = True
        pipeline.resume()
        self.assertFalse(pipeline.paused)

        stats = pipeline.run_once()
        self.assertEqual(stats.uploaded, 2)
        self.assertFalse(pipeline.paused)

    # -------------------------------------------------- 体积闸门

    def test_max_file_mb_filters_large_file(self) -> None:
        pipeline, store, _ = self.build()
        pipeline.cfg.monitor.max_file_mb = 0.05  # 50KB，pdf/xlsx 都超
        pipeline.run_once()

        for fid in ("f-pdf", "f-xlsx"):
            rec = store.get_transfer((GROUP, 102, fid))
            self.assertEqual(rec["state"], TransferState.FILTERED_OUT.value)
            self.assertIn("上限", rec["error"])

    # -------------------------------------------------- 事件

    def test_events_are_emitted(self) -> None:
        pipeline, _, events = self.build()
        pipeline.run_once()
        kinds = {e.split("|", 1)[0] for e in events}
        self.assertIn("login", kinds)
        self.assertIn("file", kinds)
        self.assertIn("stats", kinds)
        self.assertIn("progress", kinds)

    def test_start_stop_emit_state_events(self) -> None:
        pipeline, _, events = self.build()
        pipeline.start()
        pipeline.stop()
        states = [e for e in events if e.startswith("state|")]
        self.assertTrue(states, "启停应产生 state 事件")
        self.assertTrue(any("已启动" in e for e in states))
        self.assertTrue(any("已停止" in e for e in states))

    def test_stats_report_bytes(self) -> None:
        pipeline, _, _ = self.build()
        stats = pipeline.run_once()
        self.assertEqual(stats.bytes_uploaded, len(self.pdf_bytes) + len(self.xlsx_bytes))
        self.assertIn("bytes_text", stats.as_dict())

    # -------------------------------------------------- 中断恢复

    def test_interrupted_tasks_are_reset_on_start(self) -> None:
        pipeline, store, _ = self.build()
        store.claim(GroupFile(group_id=GROUP, file_id="zombie", name="x.pdf", busid=102))
        store.mark((GROUP, 102, "zombie"), TransferState.DOWNLOADING)

        pipeline.start()
        self.assertEqual(store.get_transfer((GROUP, 102, "zombie"))["state"],
                         TransferState.DISCOVERED.value)


if __name__ == "__main__":
    unittest.main()
