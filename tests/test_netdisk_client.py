"""网盘客户端探测测试。

覆盖「自动读取网盘安装地址」这条链路：注册表 → 常见目录 → 盘符扫描 → 网页兜底。
**不依赖真实注册表**：通过注入 reader / 候选路径来验证逻辑，
这样在 CI 或干净机器上也能跑。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qgb.netdisk_client import (
    CloudClient,
    KNOWN_CLIENTS,
    detect_clients,
    find_client,
    open_client_or_web,
    sync_folder_candidates,
)


class DetectClientsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # 造一个假的百度网盘安装目录
        self.baidu_dir = self.root / "BaiduNetdisk"
        self.baidu_dir.mkdir(parents=True, exist_ok=True)
        self.baidu_exe = self.baidu_dir / "BaiduNetdisk.exe"
        self.baidu_exe.write_bytes(b"MZ" + b"\x00" * 64)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_detects_from_registry(self) -> None:
        reader = lambda: {"baidu": (str(self.baidu_dir), str(self.baidu_exe))}  # noqa: E731
        clients = detect_clients(registry_reader=reader, scan_drives=False)

        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].key, "baidu")
        self.assertEqual(clients[0].name, "百度网盘")
        self.assertTrue(clients[0].installed)
        self.assertEqual(clients[0].source, "registry")
        self.assertEqual(clients[0].display_path(), str(self.baidu_exe))

    def test_registry_without_exe_is_not_installed(self) -> None:
        """注册表只给了目录、目录里没有 exe 时，必须算「未安装」并走网页版。"""
        empty = self.root / "empty"
        empty.mkdir()
        reader = lambda: {"baidu": (str(empty), "")}  # noqa: E731
        clients = detect_clients(registry_reader=reader, scan_drives=False)

        self.assertEqual(len(clients), 1)
        self.assertFalse(clients[0].installed)
        self.assertIn("未找到", clients[0].display_path())

    def test_common_path_candidate(self) -> None:
        clients = detect_clients(
            registry_reader=lambda: {},
            path_candidates={"baidu": (str(self.baidu_dir), str(self.baidu_exe))},
            scan_drives=False,
        )
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].installed)

    def test_exe_beats_directory_only(self) -> None:
        """先拿到「只有目录」的结果时，后续找到 exe 的那条必须能覆盖它。"""
        empty = self.root / "empty"
        empty.mkdir()
        clients = detect_clients(
            registry_reader=lambda: {"baidu": (str(empty), "")},
            path_candidates={"baidu": (str(self.baidu_dir), str(self.baidu_exe))},
            scan_drives=False,
        )
        self.assertTrue(clients[0].installed)
        self.assertEqual(clients[0].exe, str(self.baidu_exe))

    def test_missing_exe_does_not_overwrite_found_exe(self) -> None:
        clients = detect_clients(
            registry_reader=lambda: {"baidu": (str(self.baidu_dir), str(self.baidu_exe))},
            path_candidates={"baidu": ("C:/nope", "")},
            scan_drives=False,
        )
        self.assertTrue(clients[0].installed)

    def test_baidu_sorted_first(self) -> None:
        exe = self.baidu_exe
        reader = lambda: {  # noqa: E731
            "quark": (str(self.baidu_dir), str(exe)),
            "baidu": (str(self.baidu_dir), str(exe)),
        }
        clients = detect_clients(registry_reader=reader, scan_drives=False)
        self.assertEqual(clients[0].key, "baidu", "百度网盘必须排最前")

    def test_no_clients_at_all(self) -> None:
        clients = detect_clients(registry_reader=lambda: {}, scan_drives=False)
        self.assertEqual(clients, [])

    def test_find_client_by_key(self) -> None:
        reader = lambda: {"baidu": (str(self.baidu_dir), str(self.baidu_exe))}  # noqa: E731
        found = find_client("baidu", registry_reader=reader, scan_drives=False)
        self.assertIsNotNone(found)
        self.assertEqual(found.key, "baidu")

        missing = find_client("quark", registry_reader=reader, scan_drives=False)
        self.assertIsNone(missing)


class OpenClientOrWebTest(unittest.TestCase):
    """核心行为：**装了客户端就开客户端，没装就开网页**。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.exe = self.root / "BaiduNetdisk.exe"
        self.exe.write_bytes(b"MZ" + b"\x00" * 32)
        self.opened: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _opener(self, target: str) -> bool:
        self.opened.append(target)
        return True

    def test_prefers_client_when_installed(self) -> None:
        reader = lambda: {"baidu": (str(self.root), str(self.exe))}  # noqa: E731
        result = open_client_or_web(
            "baidu", opener=self._opener, registry_reader=reader, scan_drives=False
        )
        self.assertEqual(result["opened"], "client")
        self.assertEqual(result["target"], str(self.exe))
        self.assertEqual(self.opened, [str(self.exe)])

    def test_falls_back_to_web_when_not_installed(self) -> None:
        result = open_client_or_web(
            "baidu", opener=self._opener, registry_reader=lambda: {}, scan_drives=False
        )
        self.assertEqual(result["opened"], "web")
        self.assertEqual(result["target"], KNOWN_CLIENTS["baidu"][2])
        self.assertTrue(self.opened[0].startswith("https://"))

    def test_falls_back_to_web_when_launch_fails(self) -> None:
        """客户端存在但启动失败（被拦截/损坏）时也要能用，退回网页。"""
        reader = lambda: {"baidu": (str(self.root), str(self.exe))}  # noqa: E731
        attempts: list[str] = []

        def failing_opener(target: str) -> bool:
            attempts.append(target)
            return not target.lower().endswith(".exe")

        result = open_client_or_web(
            "baidu", opener=failing_opener, registry_reader=reader, scan_drives=False
        )
        self.assertEqual(result["opened"], "web")
        self.assertEqual(len(attempts), 2, "应先试客户端再试网页")

    def test_reports_none_when_everything_fails(self) -> None:
        result = open_client_or_web(
            "baidu",
            opener=lambda target: False,
            registry_reader=lambda: {},
            scan_drives=False,
        )
        self.assertEqual(result["opened"], "none")
        self.assertEqual(result["name"], "百度网盘")

    def test_result_never_leaks_credentials(self) -> None:
        """返回值只含路径/URL，不含任何凭据字段。"""
        result = open_client_or_web(
            "baidu", opener=self._opener, registry_reader=lambda: {}, scan_drives=False
        )
        self.assertEqual(
            set(result) <= {"opened", "target", "name", "exe", "source"}, True
        )


class SyncFolderTest(unittest.TestCase):
    def test_candidates_are_paths_and_do_not_raise(self) -> None:
        for key in ("baidu", "quark", "aliyun", "unknown"):
            found = sync_folder_candidates(key)
            self.assertIsInstance(found, list)
            for item in found:
                self.assertIsInstance(item, Path)

    def test_client_dataclass_display_path(self) -> None:
        client = CloudClient(key="baidu", name="百度网盘", exe="", install_dir="D:\\x")
        self.assertFalse(client.installed)
        # 有目录但找不到 exe 时必须说清，否则用户会以为能用
        self.assertIn("D:\\x", client.display_path())
        self.assertIn("未找到可执行文件", client.display_path())

        empty = CloudClient(key="baidu", name="百度网盘")
        self.assertIn("未找到安装位置", empty.display_path())

    def test_earliest_source_wins(self) -> None:
        """注册表先找到 exe 时，来源应显示 registry 而不是后面的 scan。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / "BaiduNetdisk.exe"
            exe.write_bytes(b"MZ" + b"\x00" * 32)
            clients = detect_clients(
                registry_reader=lambda: {"baidu": (str(root), str(exe))},
                path_candidates={"baidu": (str(root), str(exe))},
                scan_drives=False,
            )
        self.assertEqual(clients[0].source, "registry")

    def test_all_known_clients_have_web_fallback(self) -> None:
        """每个已知客户端都必须有网页兜底，否则「没装就打不开」。"""
        for key, (_name, _exes, web, _aliases) in KNOWN_CLIENTS.items():
            self.assertTrue(web.startswith("https://"), f"{key} 缺少网页版地址")


if __name__ == "__main__":
    unittest.main()
