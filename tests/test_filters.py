"""过滤规则引擎测试。"""

from __future__ import annotations

import unittest

from qgb.config import FilterConfig
from qgb.errors import ConfigError
from qgb.filters import CompiledFilters, compile_filters

MB = 1024 * 1024


class TestFilterRules(unittest.TestCase):
    def test_extension_of(self) -> None:
        self.assertEqual(CompiledFilters.extension_of("a.PDF"), "pdf")
        self.assertEqual(CompiledFilters.extension_of("a.tar.gz"), "gz")
        self.assertEqual(CompiledFilters.extension_of("noext"), "")
        self.assertEqual(CompiledFilters.extension_of("dir/深/文件.XLSX"), "xlsx")

    # -------------------------------------------------- 包含

    def test_include_match(self) -> None:
        f = compile_filters(FilterConfig(include=[r"数据_.*\.xlsx$"]))
        self.assertTrue(f.check("数据_2026Q1.xlsx", MB).accepted)
        self.assertFalse(f.check("报告.pdf", MB).accepted)

    def test_include_is_case_insensitive(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*\.pdf$"]))
        self.assertTrue(f.check("REPORT.PDF", MB).accepted)

    def test_empty_include_passes_everything(self) -> None:
        f = compile_filters(FilterConfig(include=[]))
        self.assertTrue(f.check("任意文件.xyz", 10).accepted)

    # -------------------------------------------------- 排除优先

    def test_exclude_beats_include(self) -> None:
        f = compile_filters(
            FilterConfig(include=[r".*\.xlsx$"], exclude=[r"^~\$", r"备份"])
        )
        self.assertFalse(f.check("~$数据_2026Q1.xlsx", MB).accepted)
        self.assertFalse(f.check("数据_备份.xlsx", MB).accepted)
        self.assertTrue(f.check("数据_2026Q1.xlsx", MB).accepted)

    def test_reason_is_explainable(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*\.pdf$"]))
        decision = f.check("会议记录.docx", MB)
        self.assertFalse(decision.accepted)
        self.assertIn("包含规则", decision.reason)

    # -------------------------------------------------- 扩展名白名单

    def test_extension_whitelist(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*"], extensions=["xlsx", ".pdf"]))
        self.assertTrue(f.check("a.xlsx", MB).accepted)
        self.assertTrue(f.check("b.PDF", MB).accepted)
        self.assertFalse(f.check("c.docx", MB).accepted)

    def test_extension_and_include_both_apply(self) -> None:
        f = compile_filters(
            FilterConfig(include=[r"^数据_"], extensions=["xlsx"])
        )
        self.assertTrue(f.check("数据_a.xlsx", MB).accepted)
        self.assertFalse(f.check("其他_a.xlsx", MB).accepted)
        self.assertFalse(f.check("数据_a.docx", MB).accepted)

    # -------------------------------------------------- 体积

    def test_size_bounds(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*"], min_size_mb=1, max_size_mb=10))
        self.assertFalse(f.check("tiny.bin", 512 * 1024).accepted)
        self.assertTrue(f.check("ok.bin", 5 * MB).accepted)
        self.assertFalse(f.check("huge.bin", 20 * MB).accepted)

    def test_zero_means_unlimited(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*"], min_size_mb=0, max_size_mb=0))
        self.assertTrue(f.check("zero.bin", 0).accepted)
        self.assertTrue(f.check("gigantic.bin", 9000 * MB).accepted)

    # -------------------------------------------------- 异常与自检

    def test_invalid_regex_raises_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            compile_filters(FilterConfig(include=["([unclosed"]))

    def test_selftest_shape(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*\.(pdf|xlsx)$"]))
        rows = f.selftest()
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("name", row)
            self.assertIn("accepted", row)
            self.assertIn("reason", row)

    def test_bool_decision(self) -> None:
        f = compile_filters(FilterConfig(include=[r".*\.pdf$"]))
        self.assertTrue(bool(f.check("a.pdf", 1)))
        self.assertFalse(bool(f.check("a.png", 1)))


if __name__ == "__main__":
    unittest.main()
