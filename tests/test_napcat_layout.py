"""NapCat 启动布局判定测试。

这一段守的是**「用哪种方式启动 QQ 组件」**这个决策，选错的代价很实际：

  * 把自带运行时的包误判成「挂钩布局」→ 去注入用户已安装的 QQ
    → 需要管理员、受用户 QQ 版本限制、还会改动用户的 QQ
  * 反过来把挂钩布局当成自带运行时 → 启动不了

真实背景：``NapCat.Shell.Windows.Node.zip`` 这类包里，``napcat/`` 是**负载目录**，
里面**同样有** ``NapCatWinBootMain.exe`` + ``NapCatWinBootHook.dll``。
只看「有没有这两个文件」会把它误判成挂钩布局 —— 这正是踩过的坑。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.config import NapCatConfig
from qgb.napcat.process import NapCatManager


def _touch(path: Path, content: bytes = b"MZ") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class LayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _manager(self, install: Path, qq: str = "") -> NapCatManager:
        """``install_dir`` 是只读属性（由 config 推导），因此通过配置注入。"""
        cfg = NapCatConfig(install_dir=str(install), qq_path=qq)
        return NapCatManager(cfg, self.data)

    # -------------------------------------------------- 自带运行时

    def _make_embedded(self, base: Path) -> None:
        """造一个自带运行时的布局（Node 包）。"""
        _touch(base / "node.exe")
        _touch(base / "index.js")
        _touch(base / "wrapper.node")
        # 负载目录 —— 注意它也有挂钩用的那两个文件
        _touch(base / "napcat" / "napcat.mjs")
        _touch(base / "napcat" / "NapCatWinBootMain.exe")
        _touch(base / "napcat" / "NapCatWinBootHook.dll")

    def test_embedded_is_not_treated_as_hook(self) -> None:
        base = self.data / "napcat-embedded"
        self._make_embedded(base)
        mgr = self._manager(base)
        self.assertIsNone(
            mgr.hook_layout(),
            "负载目录里的 NapCatWinBootMain.exe 不该被当成挂钩布局",
        )

    def test_embedded_runs_node_direct(self) -> None:
        base = self.data / "napcat-embedded"
        self._make_embedded(base)
        mgr = self._manager(base, qq="D:/QQ/QQ.exe")
        cmd, cwd = mgr.launcher_command()
        self.assertEqual(len(cmd), 2)
        self.assertTrue(cmd[0].lower().endswith("node.exe"))
        self.assertTrue(cmd[1].lower().endswith("index.js"))
        # 路径必须绝对化，否则 node 会按 cwd 再拼一次
        self.assertTrue(Path(cmd[0]).is_absolute())
        self.assertTrue(Path(cmd[1]).is_absolute())
        self.assertEqual(Path(cwd).resolve(), base.resolve())

    def test_embedded_wins_over_hook_when_both_present(self) -> None:
        """两种特征同时存在时，必须选自包含的 node 直调。"""
        base = self.data / "napcat"
        self._make_embedded(base)
        _touch(base / "shell" / "NapCatWinBootMain.exe")
        _touch(base / "shell" / "NapCatWinBootHook.dll")
        mgr = self._manager(base, qq="D:/QQ/QQ.exe")

        self.assertIsNotNone(mgr.hook_layout(), "shell/ 是货真价实的挂钩布局")
        cmd, _cwd = mgr.launcher_command()
        self.assertTrue(
            cmd[0].lower().endswith("node.exe"),
            "自带运行时必须优先于挂钩 —— 它不需要管理员",
        )

    def test_embedded_config_root_is_payload_dir(self) -> None:
        base = self.data / "napcat-embedded"
        self._make_embedded(base)
        mgr = self._manager(base)
        self.assertEqual(mgr.config_root().resolve(), (base / "napcat").resolve())

    # -------------------------------------------------- 挂钩布局

    def test_hook_layout_detected_in_shell_dir(self) -> None:
        base = self.data / "napcat"
        _touch(base / "shell" / "NapCatWinBootMain.exe")
        _touch(base / "shell" / "NapCatWinBootHook.dll")
        _touch(base / "shell" / "napcat.mjs")
        mgr = self._manager(base)
        hook = mgr.hook_layout()
        self.assertIsNotNone(hook)
        self.assertEqual(hook[2].resolve(), (base / "shell").resolve())

    def test_hook_command_targets_qq_and_hook_dll(self) -> None:
        base = self.data / "napcat"
        _touch(base / "shell" / "NapCatWinBootMain.exe")
        _touch(base / "shell" / "NapCatWinBootHook.dll")
        _touch(base / "shell" / "napcat.mjs")
        _touch(base / "shell" / "qqnt.json")

        mgr = self._manager(base, qq="D:/QQ/QQ.exe")
        cmd, cwd = mgr.launcher_command()
        self.assertEqual(len(cmd), 3, "挂钩模式需要 QQ 路径与 Hook DLL 两个参数")
        self.assertTrue(cmd[0].lower().endswith("napcatwinbootmain.exe"))
        self.assertIn("QQ.exe", cmd[1])
        self.assertTrue(cmd[2].lower().endswith(".dll"))
        self.assertEqual(Path(cwd).resolve(), (base / "shell").resolve())

    def test_hook_generates_loader(self) -> None:
        """launcher.bat 会用 cmd 的 echo 生成 loadNapCat.js，中文路径会坏；
        所以由 Python 生成，且路径用正斜杠（file:// URL 要求）。"""
        base = self.data / "napcat"
        _touch(base / "shell" / "NapCatWinBootMain.exe")
        _touch(base / "shell" / "NapCatWinBootHook.dll")
        _touch(base / "shell" / "napcat.mjs")
        _touch(base / "shell" / "qqnt.json")

        mgr = self._manager(base, qq="D:/QQ/QQ.exe")
        mgr.launcher_command()
        loader = base / "shell" / "loadNapCat.js"
        self.assertTrue(loader.is_file())
        text = loader.read_text(encoding="utf-8")
        self.assertIn("napcat.mjs", text)
        self.assertIn("file:///", text)
        self.assertNotIn("\\", text.split("file:///")[1], "file:// URL 里不能有反斜杠")

    def test_hook_env_vars_are_complete(self) -> None:
        base = self.data / "napcat"
        shell = base / "shell"
        _touch(shell / "NapCatWinBootMain.exe")
        _touch(shell / "NapCatWinBootHook.dll")
        _touch(shell / "napcat.mjs")
        mgr = self._manager(base)
        env = mgr.start_env(shell)
        for name in (
            "NAPCAT_PATCH_PACKAGE",
            "NAPCAT_LOAD_PATH",
            "NAPCAT_INJECT_PATH",
            "NAPCAT_LAUNCHER_PATH",
            "NAPCAT_MAIN_PATH",
        ):
            self.assertIn(name, env, f"缺少 {name}")
            self.assertTrue(env[name], f"{name} 不能为空")

    # -------------------------------------------------- 安装目录自动探测

    def test_install_dir_autodetects_embedded_runtime(self) -> None:
        """自带运行时应被**自动**认出，不靠配置项记住。

        真实教训：早先靠 config 里的 install_dir 指向它，
        程序保存配置时把该字段覆盖回空，于是又退回挂钩模式。
        """
        embedded = self.data / "napcat-embedded"
        self._make_embedded(embedded)

        cfg = NapCatConfig()          # install_dir 留空
        mgr = NapCatManager(cfg, self.data)
        self.assertEqual(mgr.install_dir.resolve(), embedded.resolve())
        self.assertIsNone(mgr.hook_layout(), "自动认出的布局同样不该是挂钩模式")

    def test_explicit_install_dir_wins(self) -> None:
        embedded = self.data / "napcat-embedded"
        self._make_embedded(embedded)
        hook_dir = self.data / "napcat"
        _touch(hook_dir / "shell" / "NapCatWinBootMain.exe")
        _touch(hook_dir / "shell" / "NapCatWinBootHook.dll")

        cfg = NapCatConfig(install_dir=str(hook_dir))
        mgr = NapCatManager(cfg, self.data)
        self.assertEqual(mgr.install_dir.resolve(), hook_dir.resolve())

    def test_falls_back_to_default_when_nothing_present(self) -> None:
        cfg = NapCatConfig()
        mgr = NapCatManager(cfg, self.data)
        self.assertEqual(mgr.install_dir, self.data / "napcat")

    def test_legacy_napcat_dir_with_runtime_also_detected(self) -> None:
        """老布局（运行时直接放在 napcat\\ 下）也要认。"""
        legacy = self.data / "napcat"
        _touch(legacy / "node.exe")
        _touch(legacy / "index.js")
        mgr = NapCatManager(NapCatConfig(), self.data)
        self.assertEqual(mgr.install_dir.resolve(), legacy.resolve())

    # -------------------------------------------------- 空目录

    def test_no_layout_at_all(self) -> None:
        base = self.data / "empty"
        base.mkdir(parents=True)
        mgr = self._manager(base)
        self.assertIsNone(mgr.hook_layout())
        cmd, _cwd = mgr.launcher_command()
        self.assertEqual(cmd, [], "既没有自带运行时也没有挂钩文件时应返回空命令")

    # -------------------------------------------------- 自带运行时缺依赖

    def test_missing_runtime_deps_detected(self) -> None:
        """缺 crypto.dll / ssl.dll 是「装了但起不来」的头号原因。

        这两个文件**不在 NapCat 包里**，来自 QQ NT 客户端；缺失时报的是
        「The specified module could not be found」，极具误导性。
        程序必须提前诊断出来，否则用户会陷入长时间排查。
        """
        base = self.data / "napcat-embedded"
        _touch(base / "node.exe")
        _touch(base / "index.js")
        _touch(base / "wrapper.node")
        _touch(base / "QQNT.dll")
        # 故意不建 crypto.dll / ssl.dll

        mgr = self._manager(base)
        missing = mgr.embedded_runtime_problems()
        self.assertEqual(len(missing), 2)
        joined = " ".join(missing)
        self.assertIn("crypto.dll", joined)
        self.assertIn("ssl.dll", joined)

        # 状态里必须把这件事说出来
        note = mgr.status().note
        self.assertIn("crypto.dll", note)
        self.assertIn("缺少", note)

    def test_complete_runtime_has_no_problems(self) -> None:
        base = self.data / "napcat-embedded"
        _touch(base / "node.exe")
        _touch(base / "index.js")
        for name in ("wrapper.node", "QQNT.dll", "crypto.dll", "ssl.dll"):
            _touch(base / name)
        mgr = self._manager(base)
        self.assertEqual(mgr.embedded_runtime_problems(), [])

    def test_dep_check_skipped_for_other_layouts(self) -> None:
        """挂钩布局不需要这些 DLL（QQ 自己带着），不该误报。"""
        base = self.data / "napcat"
        _touch(base / "shell" / "NapCatWinBootMain.exe")
        _touch(base / "shell" / "NapCatWinBootHook.dll")
        mgr = self._manager(base)
        self.assertEqual(mgr.embedded_runtime_problems(), [])


if __name__ == "__main__":
    unittest.main()
