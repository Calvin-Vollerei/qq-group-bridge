"""控制台编码的回归测试。

背景（真实事故）
----------------
``python -m qgb.dev.smoke`` 在 CI 的 Linux 机器上直接崩了：

    UnicodeEncodeError: 'charmap' codec can't encode characters in position 2-7

原因：Python 在**非交互式**环境下按区域设置挑 stdout 编码，容器里常是
``cp1252``；而本项目所有面向人的输出都是中文。Windows 控制台自 3.6 起走
UTF-8，所以本机永远复现不出来 —— 这类问题只能在「另一个平台的第一句话」
上暴露，因此必须靠闸门挡住：

1. 所有会输出中文的 CLI 入口都必须调用 UTF-8 守卫（静态检查，防止以后新增脚本又漏）
2. 守卫本身要真的能用、可重复调用、并且对没有 ``reconfigure`` 的流不报错
3. 真跑一遍 ``python -m qgb.dev.smoke``（在 CP1252 控制台下），验证端到端不崩
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 需要检查的 CLI 入口：脚本目录 + 仓库根下的启动器
ENTRY_POINTS = sorted((ROOT / "scripts").glob("*.py")) + [ROOT / "run_bridge.py"]

#: 守卫的调用形态（两种命名都接受）
GUARD_CALL = re.compile(r"^\s*(_?use_utf8_console|force_utf8_stdio)\(\)", re.M)
CJK = re.compile(r"[\u4e00-\u9fff]")


def _prints_chinese(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    return bool(CJK.search(text)) and "print(" in text


class GuardWiringTest(unittest.TestCase):
    """会输出中文的入口，必须装了 UTF-8 守卫。"""

    def test_chinese_entry_points_have_guard(self) -> None:
        missing: list[str] = []
        for path in ENTRY_POINTS:
            if path.name == "_console.py":      # 守卫自身的实现
                continue
            if not _prints_chinese(path):
                continue
            text = path.read_text(encoding="utf-8")
            if not GUARD_CALL.search(text):
                missing.append(str(path.relative_to(ROOT)).replace("\\", "/"))

        self.assertEqual(
            missing, [],
            "这些脚本会输出中文却没有切 UTF-8 控制台，在 Linux/CI 上会 "
            "UnicodeEncodeError 崩掉：\n  " + "\n  ".join(missing),
        )

    def test_guard_is_called_before_first_print(self) -> None:
        """守卫必须在第一句 print **之前**调用，否则等于没装。"""
        bad: list[str] = []
        for path in ENTRY_POINTS:
            if path.name == "_console.py" or not _prints_chinese(path):
                continue
            text = path.read_text(encoding="utf-8")
            call = GUARD_CALL.search(text)
            first_print = re.search(r"^\s*print\(", text, re.M)
            if call and first_print and first_print.start() < call.start():
                # 允许模块级调用出现在 print 之后，但要求它至少在 main() 里存在
                if not re.search(r"def main\([^)]*\)[^\n]*:\n\s*(_?use_utf8_console|force_utf8_stdio)\(\)",
                                 text):
                    bad.append(str(path.relative_to(ROOT)).replace("\\", "/"))
        self.assertEqual(bad, [], "守卫调用位置晚于第一句 print：\n  " + "\n  ".join(bad))

    def test_console_module_is_importable_standalone(self) -> None:
        """``scripts/_console.py`` 必须能被直接 import（不依赖包上下文）。"""
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            import _console  # noqa: PLC0415

            self.assertTrue(callable(_console.use_utf8_console))
        finally:
            sys.path.remove(str(ROOT / "scripts"))


class ForceUtf8StdioTest(unittest.TestCase):
    """守卫本身的行为。"""

    def test_returns_bool_and_is_idempotent(self) -> None:
        from qgb.utils import force_utf8_stdio

        first = force_utf8_stdio()
        second = force_utf8_stdio()
        self.assertIsInstance(first, bool)
        self.assertIsInstance(second, bool)
        # 连续调用不该抛异常，也不该改变结论
        self.assertEqual(first, second)

    def test_survives_stream_without_reconfigure(self) -> None:
        """有些环境里 stdout 被替换成没有 reconfigure 的对象，也不能崩。"""
        from unittest import mock

        from qgb.utils import force_utf8_stdio

        with mock.patch.object(sys, "stdout", object()):
            self.assertIsInstance(force_utf8_stdio(), bool)


class RealSmokeUnderCp1252Test(unittest.TestCase):
    """端到端：在 cp1252 控制台下真跑一次离线冒烟。

    这是唯一能真正抓住「编码没切」的检查 —— 静态检查只保证调用了守卫，
    不保证守卫有效。需要跑子进程，所以单独一条、不做太多次。
    """

    def test_smoke_survives_cp1252_console(self) -> None:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp1252"
        env["PYTHONUTF8"] = "0"          # 关掉 UTF-8 模式，逼出非 UTF-8 控制台
        # 清掉可能影响区域设置/编码的变量，模拟容器环境
        for key in ("LANG", "LC_ALL", "LC_CTYPE"):
            env.pop(key, None)

        proc = subprocess.run(
            [sys.executable, "-m", "qgb.dev.smoke"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=300,
        )
        combined = (proc.stdout or "") + (proc.stderr or "")
        self.assertNotIn("UnicodeEncodeError", combined, "中文输出仍然编不出去")
        self.assertNotIn("charmap", combined, "仍在使用 cp1252 之类的窄编码")
        self.assertEqual(proc.returncode, 0, f"冒烟失败：\n{combined[-2000:]}")
        self.assertIn("项通过", combined, "应打印中文结果行")


if __name__ == "__main__":
    unittest.main()
