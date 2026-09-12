"""`scripts/make_delivery_zip.py` 的回归测试。

交付包是**唯一会送到零基础用户手里**的东西，所以这里钉的都是"出错就没人能救"的点：

* ``_target_name``：顶层目录改名 + ``自检.bat`` → ``出错了点这个.bat``
* ``build``：内容**原样搬运**，绝不改写 .bat
  （踩过的坑：第一版按「删掉含 selftest 的行」改写 bat，把
   ``QQGroupBridge.exe --selftest --out "%REPORT%"`` 也删了，
   于是「出错了点这个.bat」双击后什么都不输出 —— 求救通道失效，
   而本机测试当时仍然是绿的，因为它只测了"文件在不在、名字对不对"）
* ``verify``：缺文件 / 混入本机数据 / 求救脚本被改坏，都必须报错
"""

from __future__ import annotations

import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import make_delivery_zip as md  # noqa: E402

APP = "QQ群文件搬运工"

SELFTEST_BAT = (
    '@echo off\r\n'
    'set "REPORT=%TEMP%\\qgb-selftest.txt"\r\n'
    'if exist "%REPORT%" del "%REPORT%" >nul 2>&1\r\n'
    'start /wait "" "QQGroupBridge.exe" --selftest --out "%REPORT%"\r\n'
    'type "%REPORT%"\r\n'
)


def _make_base_zip(path: Path, *, include_selftest: bool = True) -> Path:
    """造一个与真实完整包结构一致的最小 zip（结构对了，逻辑才测得准）。"""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{APP}/QQGroupBridge.exe", b"MZfake")
        zf.writestr(f"{APP}/portable.marker", b"")
        zf.writestr(f"{APP}/启动搬运工.bat", b'@echo off\r\nstart "" "QQGroupBridge.exe"\r\n')
        zf.writestr(f"{APP}/以管理员身份启动.bat", b"@echo off\r\nrem admin\r\n")
        zf.writestr(f"{APP}/requirements.txt", b"requests>=2.31.0\n")
        zf.writestr(f"{APP}/README.md", b"# old readme\n")
        zf.writestr(f"{APP}/使用说明.txt", b"old usage\n")
        if include_selftest:
            zf.writestr(f"{APP}/自检.bat", SELFTEST_BAT.encode("ascii"))
        zf.writestr(f"{APP}/_internal/python311.dll", b"MZfake")
    return path


class TargetNameTest(unittest.TestCase):
    def test_app_dir_is_renamed(self) -> None:
        got = md._target_name(f"{APP}/_internal/x.dll", "搬运工交付版")
        self.assertEqual(got, "搬运工交付版/_internal/x.dll")

    def test_selftest_bat_is_renamed(self) -> None:
        got = md._target_name(f"{APP}/自检.bat", APP)
        self.assertEqual(got, f"{APP}/出错了点这个.bat")

    def test_nested_selftest_is_renamed_too(self) -> None:
        got = md._target_name(f"{APP}/sub/自检.bat", APP)
        self.assertEqual(got, f"{APP}/sub/出错了点这个.bat")

    def test_other_files_untouched(self) -> None:
        for name in ("QQGroupBridge.exe", "启动搬运工.bat", "README.md", "portable.marker"):
            self.assertEqual(md._target_name(f"{APP}/{name}", APP), f"{APP}/{name}")


class BuildTest(unittest.TestCase):
    def _build(self, td: str, **kw) -> tuple[Path, Path]:
        base = _make_base_zip(Path(td) / "base.zip", **kw)
        out = Path(td) / "delivery.zip"
        md.build(base, out, APP)
        return base, out

    def test_selftest_bat_keeps_its_core_invocation(self) -> None:
        """**事故回归点**：求救脚本的关键调用绝不能被改写逻辑删掉。"""
        with TemporaryDirectory() as td:
            _base, out = self._build(td)
            with zipfile.ZipFile(out) as zf:
                text = zf.read(f"{APP}/出错了点这个.bat").decode("ascii", errors="replace")
            for needle in ("QQGroupBridge.exe", "--selftest", "--out", "%REPORT%"):
                self.assertIn(needle, text, f"求救脚本丢了 {needle}，双击后会毫无输出")

    def test_other_bats_are_copied_byte_for_byte(self) -> None:
        with TemporaryDirectory() as td:
            base, out = self._build(td)
            with zipfile.ZipFile(base) as b, zipfile.ZipFile(out) as d:
                for name in ("启动搬运工.bat", "以管理员身份启动.bat"):
                    self.assertEqual(
                        b.read(f"{APP}/{name}"), d.read(f"{APP}/{name}"),
                        f"{name} 应原样搬运",
                    )

    def test_old_selftest_name_is_gone(self) -> None:
        with TemporaryDirectory() as td:
            _base, out = self._build(td)
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            self.assertNotIn(f"{APP}/自检.bat", names)
            self.assertIn(f"{APP}/出错了点这个.bat", names)

    def test_user_docs_are_added_with_bom(self) -> None:
        with TemporaryDirectory() as td:
            _base, out = self._build(td)
            with zipfile.ZipFile(out) as zf:
                for name in ("第一步看这里.txt", "百度网盘授权怎么做.txt", "使用说明.txt"):
                    data = zf.read(f"{APP}/{name}")
                    self.assertEqual(data[:3], b"\xef\xbb\xbf", f"{name} 缺 UTF-8 BOM")

    def test_usage_doc_reference_is_rewired(self) -> None:
        """说明里不能还写着已改名的「自检.bat」，否则用户找不到文件。"""
        with TemporaryDirectory() as td:
            _base, out = self._build(td)
            with zipfile.ZipFile(out) as zf:
                usage = zf.read(f"{APP}/使用说明.txt").decode("utf-8-sig")
            # 源文件里写的是「自检.bat」，交付包里必须换成新名字
            src = (ROOT / "packaging" / "使用说明.txt").read_text(encoding="utf-8-sig")
            if "自检.bat" in src:
                self.assertNotIn("自检.bat", usage, "说明里仍引用旧文件名")


class VerifyTest(unittest.TestCase):
    def _delivery(self, td: str) -> Path:
        base = _make_base_zip(Path(td) / "base.zip")
        out = Path(td) / "delivery.zip"
        md.build(base, out, APP)
        return out

    def test_good_package_passes(self) -> None:
        with TemporaryDirectory() as td:
            out = self._delivery(td)
            # 真实 README 里没有 napcat-embedded 等禁止内容，这里只查结构类问题
            structural = [p for p in md.verify(out, APP) if "禁止内容" not in p]
            self.assertEqual(structural, [])

    def test_missing_required_file_is_reported(self) -> None:
        with TemporaryDirectory() as td:
            out = self._delivery(td)
            trimmed = Path(td) / "trimmed.zip"
            with zipfile.ZipFile(out) as src, zipfile.ZipFile(trimmed, "w") as dst:
                for info in src.infolist():
                    if info.filename.endswith("启动搬运工.bat"):
                        continue
                    dst.writestr(info, src.read(info))
            problems = md.verify(trimmed, APP)
            self.assertTrue(any("启动搬运工.bat" in p for p in problems), problems)

    def test_broken_selftest_is_reported(self) -> None:
        with TemporaryDirectory() as td:
            out = self._delivery(td)
            broken = Path(td) / "broken.zip"
            with zipfile.ZipFile(out) as src, zipfile.ZipFile(broken, "w") as dst:
                for info in src.infolist():
                    data = src.read(info)
                    if info.filename.endswith(md.SELF_TEST_NEW):
                        data = b"@echo off\r\nrem oops, the call is gone\r\n"
                    dst.writestr(info, data)
            problems = md.verify(broken, APP)
            self.assertTrue(
                any("求救脚本会失效" in p for p in problems),
                f"改坏求救脚本竟然没被抓住：{problems}",
            )

    def test_napcat_bat_is_not_flagged_for_ascii(self) -> None:
        """NapCat 自带的 .bat 里有中文，不该因此报错（只查我们自己的三个）。"""
        with TemporaryDirectory() as td:
            out = self._delivery(td)
            patched = Path(td) / "napcat.zip"
            with zipfile.ZipFile(out) as src, zipfile.ZipFile(patched, "w") as dst:
                for info in src.infolist():
                    dst.writestr(info, src.read(info))
                dst.writestr(f"{APP}/data/napcat/shell/示例.bat", "echo 中文示例\r\n".encode("utf-8"))
            problems = md.verify(patched, APP)
            self.assertFalse(
                any("非 ASCII" in p for p in problems),
                f"第三方 bat 被误报：{problems}",
            )


if __name__ == "__main__":
    unittest.main()
