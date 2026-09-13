"""下载器「卡住」检测的回归测试。

背景：用户反馈「下载失败就卡死，希望直接下一个」。排查发现问题不在于
"失败后不继续"（流水线本来就是失败即下一个），而在于**一次卡住的下载会拖很久**：

* ``requests`` 的读超时原本是 60 秒 —— 服务器接了连接却不再发数据时，
  要等满 60 秒才抛异常，再乘上重试次数，实测**105 秒**才轮到下一个文件；
* 单线程逐文件处理，所以这期间整批看起来"卡死"。

修法是把 HTTP **读超时收紧到卡死阈值**（默认 20 秒）。这里用一个
"发完一小段数据就不发了"的假服务器来验证：必须在阈值附近就放弃，
而不是拖到分钟级。
"""

from __future__ import annotations

import http.server
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path

from qgb.downloader import DownloadError, DownloadStalled, Downloader

PAYLOAD_HEAD = 8192


class _StallingHandler(http.server.BaseHTTPRequestHandler):
    """先发 8KB，然后长时间不再发数据（模拟"服务器接了连接就不动了"）。"""

    stall_seconds = 30.0

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        self.send_response(200)
        self.send_header("Content-Length", str(1024 * 1024))   # 声称 1MB
        self.end_headers()
        self.wfile.write(b"x" * PAYLOAD_HEAD)
        self.wfile.flush()
        time.sleep(self.stall_seconds)

    def log_message(self, *args: object) -> None:              # 静音
        return


class StallDetectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._srv = socketserver.TCPServer(("127.0.0.1", 0), _StallingHandler)
        cls._srv.allow_reuse_address = True
        cls.port = cls._srv.server_address[1]
        cls._thread = threading.Thread(target=cls._srv.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._srv.shutdown()
        cls._srv.server_close()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-stall-")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_stalled_download_gives_up_quickly(self) -> None:
        """卡住时必须在阈值附近放弃，而不是拖到分钟级。"""
        dl = Downloader(stall_timeout=5.0, max_retries=1, backoff_base=0.2)
        dest = Path(self._tmp.name) / "big.bin"

        started = time.monotonic()
        with self.assertRaises(DownloadError):
            dl.download(
                f"http://127.0.0.1:{self.port}/big.bin", dest,
                expected_size=1024 * 1024,
            )
        elapsed = time.monotonic() - started

        # 阈值 5 秒 + 一次重试 + 少量开销；给足余量但仍远小于旧的 105 秒
        self.assertLess(
            elapsed, 25.0,
            f"卡住后耗时 {elapsed:.1f} 秒才放弃 —— 读超时没有收紧到卡死阈值",
        )

    def test_stall_threshold_is_clamped_to_at_least_5s(self) -> None:
        """阈值下限 5 秒：太小会把慢速服务器误判成卡住。"""
        self.assertEqual(Downloader(stall_timeout=1.0).stall_timeout, 5.0)
        self.assertEqual(Downloader(stall_timeout=30.0).stall_timeout, 30.0)

    def test_check_stalled_raises_with_actionable_hint(self) -> None:
        """兜底检查本身要给出"已放弃并继续、稍后重试"这类可操作提示。"""
        dl = Downloader(stall_timeout=5.0)
        with self.assertRaises(DownloadStalled) as ctx:
            dl._check_stalled(time.monotonic() - 10.0, 12345)
        self.assertIn("卡住", ctx.exception.message)
        self.assertIn("继续下一个", getattr(ctx.exception, "hint", ""))

    def test_check_stalled_is_silent_when_data_is_fresh(self) -> None:
        dl = Downloader(stall_timeout=5.0)
        dl._check_stalled(time.monotonic(), 0)          # 不该抛


class StallIsRetryableTest(unittest.TestCase):
    def test_stalled_is_a_download_error(self) -> None:
        """必须是 DownloadError 的子类：流水线靠它走「记录失败→继续下一个」的路径。"""
        self.assertTrue(issubclass(DownloadStalled, DownloadError))


if __name__ == "__main__":
    unittest.main()
