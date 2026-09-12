"""`scripts/pack_release.py` 与扫描器白名单的回归测试。

这些规则是**安全边界**：一旦放松，本机登录痕迹（NapCat 的登录日志、
guild 库、缓存二维码）就会跟着整包发出去。所以每一条都钉死。
"""

from __future__ import annotations

import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pack_release  # noqa: E402
import scan_secrets  # noqa: E402


class RelOkTest(unittest.TestCase):
    """组件目录里哪些相对路径可以进包。"""

    def test_normal_framework_files_ship(self) -> None:
        for rel in (
            "shell/index.js",
            "shell/package.json",
            "shell/native/napi2native/napi2native.linux.x64.node",
            "shell/static/assets/index-D6lir5oM.js",
            "shell/qqnt.json",
            "bootmain/NapCatWinBootMain.exe",
        ):
            ok, why = pack_release._rel_ok(Path(rel))
            self.assertTrue(ok, f"{rel} 应该可打包，却被排除：{why}")

    def test_local_runtime_traces_are_excluded(self) -> None:
        """第一次实测抓到的真实文件名，必须全部被排除。"""
        for rel in (
            "shell/napcat.out.log",
            "shell/napcat.err.log",
            "shell/debug.log",
            "shell/guild1.db",
            "shell/guild1.db-wal",
            "shell/guild1.db-shm",
            "shell/logs/2026-09-12_11-56-09.978.log",
            "shell/cache/qrcode.png",
            "shell/config.json",
            "shell/secrets.enc",
        ):
            ok, why = pack_release._rel_ok(Path(rel))
            self.assertFalse(ok, f"{rel} 必须被排除（否则泄露本机信息）")
            self.assertTrue(why, f"{rel} 应给出排除原因")

    def test_qq_runtime_directory_never_ships(self) -> None:
        for rel in (
            "napcat-embedded/node.exe",
            "shell/napcat-embedded/index.js",
            "shell/data/state.db",
            "shell/__pycache__/x.pyc",
        ):
            ok, _ = pack_release._rel_ok(Path(rel))
            self.assertFalse(ok, f"{rel} 不该打包")


class VerifyTest(unittest.TestCase):
    """整包自检：命中禁止项必须报错。"""

    def _zip(self, tmp: Path, name: str, **entries: bytes) -> Path:
        out = tmp / name
        with zipfile.ZipFile(out, "w") as zf:
            for arc, data in entries.items():
                zf.writestr(arc.replace("__", "/"), data)
        return out

    def test_clean_package_passes(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            z = self._zip(
                tmp,
                "ok.zip",
                **{
                    "App__QQGroupBridge.exe": b"MZ",
                    "App__portable.marker": b"",
                    "App___internal___tcl_data__big5.enc": b"x" * 100,
                    "App__data__napcat__shell__index.js": b"// napcat",
                },
            )
            self.assertEqual(pack_release.verify(z), [])

    def test_local_data_is_rejected(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            z = self._zip(
                tmp,
                "bad.zip",
                **{
                    "App__data__napcat__shell__napcat.out.log": b"log",
                    "App__data__napcat__shell__guild1.db": b"db",
                    "App__data__napcat__shell__config.json": b"{}",
                },
            )
            problems = pack_release.verify(z)
            self.assertGreaterEqual(len(problems), 3)
            self.assertTrue(any("napcat.out.log" in p for p in problems))
            self.assertTrue(any("config.json" in p for p in problems))

    def test_qq_runtime_and_oversize_rejected(self) -> None:
        with TemporaryDirectory() as td:
            tmp = Path(td)
            big = pack_release.MAX_SINGLE_FILE_MB * 1024 * 1024 + 1
            out = tmp / "big.zip"
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
                # 高压缩比内容，写出少量字节即可记录一个超限的 file_size
                zf.writestr("App/data/napcat-embedded/node.exe", b"\0" * big)
            problems = pack_release.verify(out)
            self.assertTrue(any("QQ 运行时" in p or "超限" in p for p in problems))

    def test_empty_zip_rejected(self) -> None:
        with TemporaryDirectory() as td:
            z = self._zip(Path(td), "empty.zip")
            self.assertTrue(pack_release.verify(z))


class CollectNapcatTest(unittest.TestCase):
    def test_collect_skips_runtime_traces(self) -> None:
        with TemporaryDirectory() as td:
            base = Path(td) / "napcat"
            (base / "shell" / "logs").mkdir(parents=True)
            (base / "shell" / "index.js").write_text("// ok", encoding="utf-8")
            (base / "shell" / "napcat.out.log").write_text("QQ 12345", encoding="utf-8")
            (base / "shell" / "logs" / "a.log").write_text("x", encoding="utf-8")
            (base / "NapCatInstaller.exe").write_bytes(b"MZ")
            (base / "napcat-embedded").mkdir()
            (base / "napcat-embedded" / "node.exe").write_bytes(b"MZ")

            files, skipped = pack_release.collect_napcat(base)
            names = sorted(f.name for f in files)
            self.assertIn("index.js", names)
            self.assertIn("NapCatInstaller.exe", names)
            self.assertNotIn("napcat.out.log", names)
            self.assertNotIn("node.exe", names)
            self.assertTrue(any("napcat.out.log" in s for s in skipped))

    def test_missing_dir_is_reported(self) -> None:
        files, skipped = pack_release.collect_napcat(Path("Z:/definitely/not/here"))
        self.assertEqual(files, [])
        self.assertTrue(skipped)


class VendorAllowlistTest(unittest.TestCase):
    """扫描器白名单必须窄：只放行 NapCat 官方前端，别的照旧报警。"""

    def test_napcat_shell_is_vendor_runtime(self) -> None:
        self.assertTrue(
            scan_secrets.is_vendor_runtime(
                "QQ群文件搬运工/data/napcat/shell/static/assets/index-D6lir5oM.js"
            )
        )
        self.assertTrue(
            scan_secrets.is_vendor_runtime("App/data/napcat/shell/qqnt.json")
        )

    def test_our_own_files_are_not_vendor(self) -> None:
        for name in (
            "App/_internal/qgb/config.py",
            "App/data/config.json",
            "qgb/secrets.py",
            "App/data/napcat/napcat-start.log",
        ):
            self.assertFalse(
                scan_secrets.is_vendor_runtime(name), f"{name} 不该被当成第三方运行时"
            )

    def test_private_key_allowance_is_narrow(self) -> None:
        js = "QQ群文件搬运工/data/napcat/shell/static/assets/index-D6lir5oM.js"
        self.assertTrue(scan_secrets.is_vendor_bundle(js))
        # 同一目录下的 .json / .html 不放行
        self.assertFalse(
            scan_secrets.is_vendor_bundle("App/data/napcat/shell/static/qqnt.json")
        )
        # 我们的源码与 PEM 文件绝不放行
        self.assertFalse(scan_secrets.is_vendor_bundle("qgb/keys/server.pem"))
        self.assertFalse(scan_secrets.is_vendor_bundle("App/_internal/x.js"))

    def test_real_private_key_still_caught(self) -> None:
        """真正的私钥字面量在普通路径里必须仍然被抓到。"""
        pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END PRIVATE KEY-----"
        findings = scan_secrets.scan_text(pem, "qgb/somewhere.py")
        self.assertTrue(any("私钥" in f.rule for f in findings), "私钥规则失效了")

    def test_private_key_marker_in_vendor_bundle_is_silent(self) -> None:
        """第三方压缩 JS 里的字符串常量不应报错（实测的真实误报）。"""
        js = 'const t="-----BEGIN PRIVATE KEY-----";export{t};'
        rules = tuple(r for r in scan_secrets.VENDOR_RULES if "私钥" not in r.name)
        self.assertEqual(scan_secrets.scan_text(js, "vendor.js", rules), [])


if __name__ == "__main__":
    unittest.main()
