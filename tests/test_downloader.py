"""下载器测试：断点续传、服务器忽略 Range、体积校验、直链过期。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.dev.fakes import RangeFileServer
from qgb.downloader import DownloadProgress, Downloader
from qgb.errors import DownloadError

CONTENT = (b"QGB-DOWNLOAD-TEST\x00" * 8000)  # ~150KB


class TestDownloader(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-dl-")
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "src.bin"
        self.src.write_bytes(CONTENT)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dl(self, **kw) -> Downloader:
        kw.setdefault("max_retries", 1)
        kw.setdefault("backoff_base", 0)
        return Downloader(**kw)

    # -------------------------------------------------- 基础

    def test_plain_download(self) -> None:
        with RangeFileServer({"src.bin": self.src}) as srv:
            dest = self.tmp / "out.bin"
            result = self._dl().download(srv.url_for("src.bin"), dest)

            self.assertTrue(dest.is_file())
            self.assertEqual(dest.read_bytes(), CONTENT)
            self.assertEqual(result.size, len(CONTENT))
            self.assertEqual(result.resumed_from, 0)

    def test_sha256_is_correct(self) -> None:
        import hashlib

        with RangeFileServer({"src.bin": self.src}) as srv:
            result = self._dl().download(srv.url_for("src.bin"), self.tmp / "out.bin")
            self.assertEqual(result.sha256, hashlib.sha256(CONTENT).hexdigest())

    def test_part_file_cleaned_up(self) -> None:
        with RangeFileServer({"src.bin": self.src}) as srv:
            dest = self.tmp / "out.bin"
            self._dl().download(srv.url_for("src.bin"), dest)
            self.assertFalse(dest.with_name(dest.name + ".part").exists())

    def test_progress_callback_receives_final_total(self) -> None:
        seen: list[DownloadProgress] = []
        with RangeFileServer({"src.bin": self.src}) as srv:
            self._dl().download(
                srv.url_for("src.bin"), self.tmp / "out.bin", on_progress=seen.append
            )
        self.assertTrue(seen)
        last = seen[-1]
        self.assertEqual(last.total, len(CONTENT))

    # -------------------------------------------------- 断点续传

    def test_resume_uses_range(self) -> None:
        half = len(CONTENT) // 2
        dest = self.tmp / "resume.bin"
        dest.with_name(dest.name + ".part").write_bytes(CONTENT[:half])

        with RangeFileServer({"src.bin": self.src}, support_range=True) as srv:
            result = self._dl().download(
                srv.url_for("src.bin"), dest, expected_size=len(CONTENT)
            )

        self.assertEqual(dest.read_bytes(), CONTENT)
        self.assertEqual(result.resumed_from, half, "应从半截处续传")

    def test_complete_part_is_promoted_without_redownload(self) -> None:
        dest = self.tmp / "done.bin"
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(CONTENT)

        with RangeFileServer({"src.bin": self.src}) as srv:
            result = self._dl().download(srv.url_for("src.bin"), dest, expected_size=len(CONTENT))

        self.assertTrue(dest.is_file())
        self.assertFalse(part.exists())
        self.assertEqual(result.resumed_from, 0)
        self.assertEqual(result.size, len(CONTENT))

    def test_server_ignoring_range_restarts_cleanly(self) -> None:
        """服务器不支持 Range 时必须从头写，否则会得到「半截 + 整份」的损坏文件。"""
        half = len(CONTENT) // 2
        dest = self.tmp / "ignored.bin"
        dest.with_name(dest.name + ".part").write_bytes(CONTENT[:half])

        with RangeFileServer({"src.bin": self.src}, support_range=False) as srv:
            self._dl().download(srv.url_for("src.bin"), dest, expected_size=len(CONTENT))

        self.assertEqual(dest.read_bytes(), CONTENT)
        self.assertEqual(dest.stat().st_size, len(CONTENT))

    # -------------------------------------------------- 失败路径

    def test_size_mismatch_raises_and_keeps_part(self) -> None:
        with RangeFileServer({"src.bin": self.src}) as srv:
            dest = self.tmp / "bad.bin"
            with self.assertRaises(DownloadError):
                self._dl().download(
                    srv.url_for("src.bin"), dest, expected_size=len(CONTENT) + 9999
                )
            # 保留 .part 以便下次续传
            self.assertTrue(dest.with_name(dest.name + ".part").exists())
            self.assertFalse(dest.exists())

    def test_missing_file_raises_signature_error(self) -> None:
        with RangeFileServer({"src.bin": self.src}) as srv:
            with self.assertRaises(DownloadError) as ctx:
                self._dl().download(srv.url_for("nope.bin"), self.tmp / "x.bin")
            self.assertIn("过期", str(ctx.exception))

    def test_unreachable_host_raises_download_error(self) -> None:
        with self.assertRaises(DownloadError):
            self._dl().download("http://127.0.0.1:1/none", self.tmp / "y.bin")

    def test_truncated_stream_is_detected(self) -> None:
        """中途断流：大小不符必须报错，而不是静默产出损坏文件。"""
        with RangeFileServer({"src.bin": self.src}, choke_after=1024) as srv:
            dest = self.tmp / "trunc.bin"
            with self.assertRaises(DownloadError):
                self._dl().download(
                    srv.url_for("src.bin"), dest, expected_size=len(CONTENT)
                )


if __name__ == "__main__":
    unittest.main()
