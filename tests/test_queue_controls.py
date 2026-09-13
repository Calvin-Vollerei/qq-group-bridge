"""「刷新二维码」与「下载顺序」的控制器行为回归测试。

两个都源自用户实测反馈：

1. **点「获取二维码」没反应** —— ``run_async`` 对同名任务静默返回 False，
   而界面在 False 时什么都不做。单次取码最长 25 秒、二维码 30 秒失效，
   用户必然连点，于是每次都被丢弃。现在改成「新的取代旧的」。
2. **想自己安排下载顺序** —— 新增置顶 / 上移下移，且必须立即生效。
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from qgb import paths
from qgb.models import GroupFile
from qgb.napcat.qr import QrCode


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-qr-")
        self.tmp = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {"QGB_DATA_DIR": str(self.tmp / "data")},
                                    clear=False)
        self._env.start()
        paths.reset_cache()
        import qgb.controller as C

        self.C = C
        self.controller = C.AppController(config_path=self.tmp / "config.json",
                                          log_dir=self.tmp / "logs")
        self.controller.load()

    def tearDown(self) -> None:
        try:
            self.controller.shutdown()
        except Exception:
            pass
        # 必须先释放日志文件句柄：Windows 上 SQLite/日志文件被占用时
        # TemporaryDirectory.cleanup() 会抛 NotADirectoryError（看不出真因）。
        try:
            from qgb.logging_setup import reset_logging_for_tests

            reset_logging_for_tests()
        except Exception:
            pass
        self._env.stop()
        paths.reset_cache()
        self._tmp.cleanup()


class QrRefreshTest(_Base):
    """**回归**：连点「获取二维码」不能再被静默丢弃。"""

    def test_every_click_starts_a_request(self) -> None:
        seen: list[int] = []

        def fake(webui, token, *, napcat_dir=None, **kw):
            seen.append(1)
            time.sleep(0.3)
            return QrCode(png_bytes=b"\\x89PNG-fake", source="fake")

        with mock.patch.object(self.C, "fetch_qrcode", fake):
            first = self.controller.fetch_qrcode()
            time.sleep(0.05)
            second = self.controller.fetch_qrcode()      # 旧版这里返回 False
            time.sleep(1.0)

        self.assertTrue(first, "第一次点击应启动请求")
        self.assertTrue(second, "第二次点击也必须启动 —— 否则就是「点了没反应」")
        self.assertEqual(len(seen), 2, "两次点击都应真的发出去")

    def test_pending_event_gives_immediate_feedback(self) -> None:
        """发起后要立刻回一条 pending 事件，界面才有反馈（不必等网络）。"""
        def slow(webui, token, *, napcat_dir=None, **kw):
            time.sleep(0.5)
            return QrCode(png_bytes=b"x", source="fake")

        with mock.patch.object(self.C, "fetch_qrcode", slow):
            self.controller.fetch_qrcode()
            events = [e for e in self.controller.drain() if e.kind == "qr"]
            self.assertTrue(events, "应立刻收到事件")
            self.assertTrue(events[0].data.get("pending"), "第一条应是 pending 提示")
            self.assertIn("正在获取", events[0].message)
            time.sleep(0.8)

    def test_stale_result_does_not_overwrite_newer_one(self) -> None:
        """慢的旧请求返回时，不能覆盖更快的新请求的结果。"""
        order: list[int] = []

        def by_speed(webui, token, *, napcat_dir=None, **kw):
            n = len(order)
            order.append(n)
            time.sleep(0.5 if n == 0 else 0.1)
            return QrCode(png_bytes=b"x", source=f"req{n}")

        with mock.patch.object(self.C, "fetch_qrcode", by_speed):
            self.controller.fetch_qrcode()          # 慢
            time.sleep(0.05)
            self.controller.fetch_qrcode()          # 快，应胜出
            time.sleep(1.0)

        self.assertEqual(self.controller.qrcode.source, "req1",
                         "界面应持有最新那次请求的结果")
        posted = [e for e in self.controller.drain() if e.kind == "qr" and not e.data.get("pending")]
        self.assertTrue(all(e.data.get("seq") == 2 for e in posted),
                        "旧请求的结果不该被投递给界面")

    def test_age_is_reported_for_expiry_hint(self) -> None:
        def fake(webui, token, *, napcat_dir=None, **kw):
            return QrCode(png_bytes=b"x", source="fake")

        self.assertEqual(self.controller.qrcode_age_sec, -1.0, "还没取过时返回 -1")
        with mock.patch.object(self.C, "fetch_qrcode", fake):
            self.controller.fetch_qrcode()
            time.sleep(0.4)
        self.assertGreaterEqual(self.controller.qrcode_age_sec, 0.0)


class RunAsyncForceTest(_Base):
    def test_without_force_second_call_is_rejected(self) -> None:
        """默认仍拒绝重入（避免重复副作用），但要能区分出"被拒绝"。"""
        release = [False]

        def slow() -> str:
            while not release[0]:
                time.sleep(0.02)
            return "done"

        self.assertTrue(self.controller.run_async("t", slow))
        self.assertFalse(self.controller.run_async("t", slow), "默认应拒绝重入")
        release[0] = True
        time.sleep(0.3)

    def test_force_allows_reentry(self) -> None:
        release = [False]

        def slow() -> str:
            while not release[0]:
                time.sleep(0.02)
            return "done"

        self.assertTrue(self.controller.run_async("t", slow))
        self.assertTrue(self.controller.run_async("t", slow, force=True),
                        "force=True 应放行")
        release[0] = True
        time.sleep(0.3)


class QueueOrderTest(_Base):
    """**回归**：置顶 / 上移下移，且顺序即下载顺序。"""

    def setUp(self) -> None:
        super().setUp()
        for i, name in enumerate(("a.pdf", "b.pdf", "c.pdf", "d.pdf")):
            gf = GroupFile(group_id="111", file_id=f"f{i}", name=name, size=100, busid=1)
            self.controller.store.upsert_file(gf)
            self.controller.store.claim(gf)

    def names(self) -> list[str]:
        return [r["name"] for r in self.controller.queue_ordered()]

    def test_default_order_is_discovery_order(self) -> None:
        self.assertEqual(self.names(), ["a.pdf", "b.pdf", "c.pdf", "d.pdf"])

    def test_move_up_and_down(self) -> None:
        self.assertTrue(self.controller.queue_move(("111", 1, "f1"), -1))
        self.assertEqual(self.names(), ["b.pdf", "a.pdf", "c.pdf", "d.pdf"])
        self.assertTrue(self.controller.queue_move(("111", 1, "f1"), +1))
        self.assertEqual(self.names(), ["a.pdf", "b.pdf", "c.pdf", "d.pdf"])

    def test_move_at_edge_returns_false(self) -> None:
        self.assertFalse(self.controller.queue_move(("111", 1, "f0"), -1), "首位不能再上移")
        self.assertFalse(self.controller.queue_move(("111", 1, "f3"), +1), "末位不能再下移")

    def test_pin_goes_first_and_unpin_keeps_position(self) -> None:
        self.controller.queue_pin(("111", 1, "f2"), True)
        self.assertEqual(self.names(), ["c.pdf", "a.pdf", "b.pdf", "d.pdf"])

        self.controller.queue_pin(("111", 1, "f2"), False)
        self.assertEqual(self.names()[0], "c.pdf",
                         "取消置顶后应留在未置顶段最前，而不是掉到队尾")
        self.assertFalse(self.controller.queue_ordered()[0]["pinned"])

    def test_move_does_not_cross_pinned_boundary(self) -> None:
        self.controller.queue_pin(("111", 1, "f2"), True)         # c 置顶
        self.controller.queue_move(("111", 1, "f0"), -1)          # a 想上移到 c 之前
        order = self.names()
        self.assertEqual(order[0], "c.pdf", "置顶项不该被普通上移挤下去")
        self.assertTrue(self.controller.queue_ordered()[0]["pinned"])

    def test_unknown_key_is_ignored(self) -> None:
        self.assertFalse(self.controller.queue_move(("111", 1, "nope"), -1))
        self.assertFalse(self.controller.queue_pin(("111", 1, "nope"), True))
        self.assertEqual(self.names(), ["a.pdf", "b.pdf", "c.pdf", "d.pdf"])

    def test_order_is_what_pipeline_will_consume(self) -> None:
        """流水线取的就是 ``pending_ordered``，与界面展示必须一致。"""
        self.controller.queue_pin(("111", 1, "f3"), True)
        ui_order = self.names()
        pipeline_order = [r["name"] for r in self.controller.store.pending_ordered()]
        self.assertEqual(ui_order, pipeline_order)


if __name__ == "__main__":
    unittest.main()
