"""仓库卫生回归测试：防止「源码文件被 .gitignore 静默吃掉」。

背景（真实事故）
----------------
``.gitignore`` 里曾有一条 ``napcat/``。Git 对**不带斜杠前缀**的目录模式
会匹配**任意层级**的同名目录，于是源码包 ``qgb/napcat/`` 被一起忽略了：

* 本地一切正常 —— 文件就在磁盘上，跟 Git 无关；
* 首次克隆 / CI 上则直接 ``ModuleNotFoundError: No module named 'qgb.napcat'``，
  4 个测试模块整体 import 失败（205 项跑不了 92 项）；
* 而且推了两次都没人发现，因为**构建产物**是由 PyInstaller 从工作区直接
  打包的，同样不经过 Git。

所以这里用真实 Git 判定一次：所有源码文件都必须可被跟踪。
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 必须被跟踪（且必须存在）的源码目录
SOURCE_DIRS = ("qgb", "tests")

#: 故意要被忽略的路径 —— 运行时产物、凭据、第三方组件
MUST_BE_IGNORED = (
    "data/secrets.enc",
    "data/state.db",
    "data/napcat/index.js",
    "data/napcat-embedded/node.exe",
    "logs/app.log",
    ".downloads/NapCat.zip",
    "dist/whatever.zip",
    "qgb/__pycache__/client.cpython-311.pyc",
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _check_ignore(*paths: str) -> subprocess.CompletedProcess[str]:
    """按路径本身判断是否被忽略。

    ``--no-index`` 不能省：``git check-ignore`` 默认**跳过已跟踪的文件**
    （因为对它们来说忽略规则已无意义）。但本测试要防的恰恰是
    「文件虽然被跟踪，却因为规则写得太宽而**根本加不进来**」——
    没有 ``--no-index`` 时，主断言一旦等文件进了索引就永远为真，形同虚设
    （实测：注入旧规则 ``napcat/`` 也照样通过）。
    """
    return _git("check-ignore", "--no-index", "--", *paths)


def _git_available() -> bool:
    if not (ROOT / ".git").exists():
        return False
    try:
        return _git("rev-parse", "--git-dir").returncode == 0
    except OSError:  # git 不在 PATH 上
        return False


@unittest.skipUnless(_git_available(), "需要 git 仓库与 git 命令")
class SourceFilesAreTrackableTest(unittest.TestCase):
    """源码文件绝不能被忽略规则命中。"""

    def test_no_source_file_is_ignored(self) -> None:
        files = [
            str(p.relative_to(ROOT)).replace("\\", "/")
            for d in SOURCE_DIRS
            for p in sorted((ROOT / d).rglob("*.py"))
            if "__pycache__" not in p.parts
        ]
        self.assertGreater(len(files), 50, "源码文件数量异常，检查测试是否在仓库根运行")

        result = _check_ignore(*files)
        ignored = [line for line in result.stdout.splitlines() if line.strip()]
        self.assertEqual(
            ignored,
            [],
            "这些源码文件被 .gitignore 忽略了，克隆后必然 ImportError：\n  "
            + "\n  ".join(ignored),
        )

    def test_napcat_package_is_not_ignored(self) -> None:
        """事故回归点：qgb/napcat/ 是**源码包**，不是要排除的第三方运行时。"""
        for rel in ("qgb/napcat/__init__.py", "qgb/napcat/client.py",
                    "qgb/napcat/process.py", "qgb/napcat/qr.py"):
            self.assertTrue((ROOT / rel).exists(), f"{rel} 不存在")
            result = _git("check-ignore", "--no-index", "-v", "--", rel)
            self.assertNotEqual(
                result.returncode, 0,
                f"{rel} 被忽略规则命中：{result.stdout.strip()}",
            )

    def test_napcat_package_is_importable(self) -> None:
        import importlib  # noqa: PLC0415

        for name in ("qgb.napcat", "qgb.napcat.client", "qgb.napcat.process",
                     "qgb.napcat.qr"):
            importlib.import_module(name)


@unittest.skipUnless(_git_available(), "需要 git 仓库与 git 命令")
class IgnoreRulesStillWorkTest(unittest.TestCase):
    """修窄规则的同时，运行时与凭据**必须**继续被忽略。"""

    def test_runtime_and_secret_paths_are_ignored(self) -> None:
        result = _check_ignore(*MUST_BE_IGNORED)
        hits = {line.strip() for line in result.stdout.splitlines() if line.strip()}
        for rel in MUST_BE_IGNORED:
            self.assertIn(rel, hits, f"{rel} 居然没被忽略 —— 这是泄露风险")

    def test_third_party_dirs_are_ignored_by_anchored_rule(self) -> None:
        """第三方组件目录必须被忽略，且规则不能是裸目录名。

        注意 ``git check-ignore`` 的退出码语义：**0 = 被忽略**，1 = 没被忽略。
        （``data/`` 已经覆盖了整个数据目录，所以 ``data/napcat/`` 未必需要
        单独一条规则；这里只保证「真的会被忽略」且「没有裸目录名规则」。）
        """
        result = _git("check-ignore", "--no-index", "-v", "--", "data/napcat/index.js")
        self.assertEqual(result.returncode, 0, "data/napcat/ 必须被忽略")

        # 规则形如 '.gitignore:29:data/  data/napcat/index.js'
        pattern = result.stdout.split("\t")[0]
        pattern = pattern.split(":", 2)[-1].strip() if ":" in pattern else pattern
        self.assertNotEqual(
            pattern, "napcat/",
            "裸目录名 napcat/ 会连源码包 qgb/napcat/ 一起忽略（真实事故）",
        )


if __name__ == "__main__":
    unittest.main()
