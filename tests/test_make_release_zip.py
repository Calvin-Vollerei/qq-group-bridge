"""可分发包的打包规则测试。

这一组测试守的是**安全闸门本身**：分发包里绝不能出现本机凭据，
同时也不能因为规则过宽而误伤运行库 —— 两种错误都真实发生过：

  * 过窄 → 把 ``data\\``（含 ``secrets.enc``）发出去 = 泄露账号
  * 过宽 → 把 Tcl 的 ``big5.enc`` 编码表剔掉 = 打包后中文显示出问题
"""

from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from make_release_zip import (  # noqa: E402
    NEVER_SHIP_NAMES,
    RUNTIME_TOP_LEVEL,
    build_zip,
    should_ship,
    verify_no_runtime_leak,
)


class ShouldShipTest(unittest.TestCase):
    def test_runtime_data_never_ships(self) -> None:
        """data\\ 是运行期数据，里面可能就有凭据库。"""
        self.assertFalse(should_ship(Path("data/secrets.enc")))
        self.assertFalse(should_ship(Path("data/config.json")))
        self.assertFalse(should_ship(Path("data/state.db")))
        self.assertFalse(should_ship(Path("data/napcat/shell/napcat.mjs")))

    def test_logs_never_ship(self) -> None:
        self.assertFalse(should_ship(Path("logs/app.log")))

    def test_secret_files_excluded_anywhere(self) -> None:
        """非便携部署时凭据可能落在别处，按确切名字兜底拦住。"""
        for name in ("secrets.enc", "state.db", "state.db-wal", "config.json",
                     "qrcode.png"):
            self.assertFalse(should_ship(Path(name)), name)
            self.assertFalse(should_ship(Path("sub") / name), name)

    def test_state_db_rolling_files_excluded(self) -> None:
        self.assertFalse(should_ship(Path("state.db-20260912")))

    def test_shippable_payload(self) -> None:
        for rel in ("QQGroupBridge.exe", "portable.marker", "启动搬运工.bat",
                    "_internal/python311.dll", "使用说明.txt", "README.md",
                    "start-napcat-as-admin.bat"):
            self.assertTrue(should_ship(Path(rel)), rel)

    def test_tcl_encoding_tables_must_ship(self) -> None:
        """**回归测试**：Tcl 的 .enc 是编码表，不是凭据，必须发。

        早先的规则写的是「排除所有 *.enc」，结果把它们剔掉了 ——
        那会让打包后的界面出现中文编码问题。
        """
        for name in ("ascii.enc", "big5.enc", "cp1252.enc", "cns11643.enc"):
            rel = Path("_internal/_tcl_data/encoding") / name
            self.assertTrue(should_ship(rel), f"{name} 是 Tcl 编码表，必须分发")

    def test_vendor_dlls_ship(self) -> None:
        for rel in ("_internal/python311.dll", "_internal/_tk_data/tk.tcl",
                    "_internal/PIL/_imaging.cp311-win_amd64.pyd"):
            self.assertTrue(should_ship(Path(rel)), rel)


class BuildZipTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.release = self.root / "QQ群文件搬运工"
        (self.release / "_internal").mkdir(parents=True)
        (self.release / "data" / "napcat").mkdir(parents=True)

        # 该发的
        (self.release / "QQGroupBridge.exe").write_bytes(b"MZ" + b"\x00" * 64)
        (self.release / "portable.marker").write_text("", encoding="utf-8")
        (self.release / "启动搬运工.bat").write_text("@echo off\n", encoding="ascii")
        (self.release / "_internal" / "python311.dll").write_bytes(b"MZ" + b"\x00" * 32)
        enc = self.release / "_internal" / "big5.enc"
        enc.write_bytes(b"tcl-encoding-table")

        # 不该发的
        (self.release / "data" / "secrets.enc").write_bytes(b"QGB1-encrypted")
        (self.release / "data" / "config.json").write_text('{"groups":["1"]}',
                                                           encoding="utf-8")
        (self.release / "data" / "state.db").write_bytes(b"SQLite")
        (self.release / "data" / "qrcode.png").write_bytes(b"\x89PNG")

        self.zip_path = self.root / "out.zip"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_zip_excludes_credentials_and_keeps_payload(self) -> None:
        out, count, excluded = build_zip(self.release, self.zip_path)

        self.assertTrue(out.is_file())
        self.assertIn("data", excluded)
        self.assertGreaterEqual(count, 5)

        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()

        joined = "\n".join(names)
        # 该在的
        self.assertTrue(any(n.endswith("QQGroupBridge.exe") for n in names))
        self.assertTrue(any(n.endswith("portable.marker") for n in names))
        self.assertTrue(any(n.endswith("python311.dll") for n in names))
        # **关键**：Tcl 编码表必须在内
        self.assertTrue(any(n.endswith("big5.enc") for n in names),
                        "Tcl 编码表被误排除，会导致中文显示异常")
        # 不该在的
        for bad in ("secrets.enc", "state.db", "config.json", "qrcode.png"):
            self.assertNotIn(bad, joined, f"{bad} 不该进分发包")
        self.assertNotIn("/data/", joined.replace("\\", "/"))

    def test_verify_detects_leaks(self) -> None:
        """自检必须真的能抓到混入的凭据 —— 否则闸门是摆设。"""
        leaky = self.root / "leaky.zip"
        with zipfile.ZipFile(leaky, "w") as zf:
            zf.writestr("QQ群文件搬运工/data/secrets.enc", "QGB1")
            zf.writestr("QQ群文件搬运工/state.db", "SQLite")

        problems = verify_no_runtime_leak(leaky)

        # 不锁死条数：data/secrets.enc 会同时命中「运行期目录」和
        # 「凭据文件」两条规则，这是**正确**的重复告警，不该被断言绑住。
        self.assertTrue(problems, "混入凭据必须被检出")
        joined = " ".join(problems)
        self.assertIn("secrets.enc", joined)
        self.assertIn("state.db", joined)
        self.assertTrue(any("运行期目录" in p for p in problems))
        self.assertTrue(any("凭据" in p for p in problems))

    def test_verify_passes_clean_zip(self) -> None:
        out, _count, _excluded = build_zip(self.release, self.zip_path)
        self.assertEqual(verify_no_runtime_leak(out), [])

    def test_exclusion_sets_are_documented(self) -> None:
        """不许出现「什么都排除」的过宽配置。"""
        self.assertIn("data", RUNTIME_TOP_LEVEL)
        self.assertIn("secrets.enc", NEVER_SHIP_NAMES)
        # .enc 不能整类排除（Tcl 编码表就是 .enc）
        self.assertNotIn(".enc", NEVER_SHIP_NAMES)


if __name__ == "__main__":
    unittest.main()
