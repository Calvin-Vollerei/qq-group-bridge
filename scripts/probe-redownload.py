"""实测：**下载成功 + 上传失败**之后重试，会不会重新下载？

背景（真实事故）：用户库里清出 14GB 临时文件，其中**同一个 582MB 文件出现 129 次**
（21:03、22:49、23:09、23:31…）。原因是它上传一直 405 失败，
而每次重试都会**从零重新下载** —— 因为 ``except DownloadError`` 分支里
用 ``local_path.unlink()`` 把**已下好的完整副本**也删了。

本探针用一个"上传必然失败"的假上传器复现该路径，并验证修复后
第二轮**跳过下载、复用本地副本**。

用法::

    python scripts/probe-redownload.py
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402

#: 探针输出缓冲。
#:
#: ⚠️ 探针内部**不直接 print**：有测试要求 ``use_utf8_console()`` 必须早于
#: 第一句 print（tests/test_console_encoding.py 的 guard 用例），而类方法里的
#: print 会被判为"早于守卫"。统一收集，由 main() 在守卫之后输出。
_OUT: list[str] = []


def say(text: str = "") -> None:
    _OUT.append(text)


class _FailingUploader:
    """上传一律失败（模拟 405）。"""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def upload(self, local_path, remote_path, **kw):
        from qgb.errors import UploadError

        raise UploadError("模拟上传失败（405）")

    def remote_size(self, *a, **kw):
        return None

    def ensure_dir(self, *a, **kw):
        return None


class RedownloadProbe(unittest.TestCase):
    def runTest(self) -> None:  # noqa: N802
        from qgb.models import TransferState
        from tests.test_pipeline import GROUP, PipelineCase

        case = PipelineCase("test_end_to_end_uploads_matching_files")
        case.setUp()
        try:
            pipeline, store, events = case.build()
            pipeline.uploader = _FailingUploader(pipeline.uploader)

            pipeline.run_once()
            downloads1 = [e for e in events if "开始下载" in e]
            tmp = Path(pipeline.tmp_dir)
            copies1 = [f for f in tmp.rglob("*") if f.is_file()]
            say(f"  第 1 轮（上传失败）：下载 {len(downloads1)} 次；"
                f"本地副本 {len(copies1)} 个")
            for f in copies1:
                say(f"      {f.name}  {f.stat().st_size / 1024:.1f} KB")

            for fid in ("f-pdf", "f-xlsx"):
                store.mark((GROUP, 102, fid), TransferState.DISCOVERED)
            events.clear()

            pipeline.run_once()
            downloads2 = [e for e in events if "开始下载" in e]
            skips = [e for e in events if "跳过下载" in e]
            copies2 = [f for f in tmp.rglob("*") if f.is_file()]
            say(f"  第 2 轮（重试）：下载 {len(downloads2)} 次；"
                f"跳过下载 {len(skips)} 次；本地副本 {len(copies2)} 个")
            say()

            if not downloads2 and skips:
                say("  ✅ 复用了本地副本，没有重复下载")
            elif downloads2:
                say(f"  ❌ 又重下了 {len(downloads2)} 次 —— 大文件会反复白下")
                for d in downloads2:
                    say(f"      {d}")
            else:
                say("  ⚠️ 既没下载也没跳过，检查用例路径")

            say()
            say(f"  keep_local_days = {pipeline.cfg.monitor.keep_local_days}")
        finally:
            case.tearDown()


def main() -> int:
    """入口：守卫 → 标题 → 跑探针 → 输出了缓冲。"""
    use_utf8_console()          # 必须在第一句 print 之前（有测试盯着）
    print("=" * 72)
    print("实测：下载成功 + 上传失败 → 重试时是否重下")
    print("=" * 72)
    result = unittest.TextTestRunner(verbosity=0).run(
        unittest.TestSuite([RedownloadProbe()])
    )
    for line in _OUT:
        print(line)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
