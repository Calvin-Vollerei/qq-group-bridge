"""OpenList 进程管理测试。

守的是「本地自动关联启动」这个能力。它有两个容易做错的地方，
都会造成**很难排查**的后果：

  * 把远端（云服务器）的 WebDAV 地址也当成"该启动本地进程" → 白白拉起
    一个用不上的实例；
  * 退出时按进程名去杀 → 把**用户自己启动的**实例（甚至同名的别的程序）杀掉。
"""

from __future__ import annotations

import socket
import tempfile
import unittest
from pathlib import Path

from qgb.config import UploadConfig
from qgb.errors import QgbError
from qgb.openlist import OpenListManager, openlist_candidates


def _free_port() -> int:
    """拿一个几乎肯定没被占用的端口。

    **测试绝不能依赖固定端口**（例如 5244）：开发机上很可能真的跑着
    OpenList，那样 `is_running()` 会返回 True，测试就被真实服务干扰了 ——
    实测踩到过，表现为一堆莫名其妙的失败。
    """
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.close()
    return port


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-ol-")
        self.root = Path(self._tmp.name)
        self.app_base = self.root / "app"
        self.data_dir = self.root / "data"
        self.app_base.mkdir(parents=True)
        self.data_dir.mkdir(parents=True)
        self.port = _free_port()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _mgr(self, webdav_url: str = "", openlist_dir: str = "") -> OpenListManager:
        # 默认指向一个空闲端口，避免撞上开发机上真实运行的 OpenList
        url = webdav_url or f"http://127.0.0.1:{self.port}/dav"
        cfg = UploadConfig(webdav_url=url, openlist_dir=openlist_dir)
        return OpenListManager(cfg, app_base=self.app_base, data_dir=self.data_dir)

    def _install_fake(self, name: str = "openlist.exe") -> Path:
        directory = self.app_base / "OpenList"
        directory.mkdir(parents=True, exist_ok=True)
        exe = directory / name
        exe.write_bytes(b"MZ" + b"\x00" * 32)
        return directory


class TargetResolutionTest(_Base):
    def test_local_hosts_are_managed(self) -> None:
        # IPv6 字面量在 URL 里必须加方括号，否则会被解析坏
        for host in ("127.0.0.1", "localhost", "[::1]"):
            mgr = self._mgr(f"http://{host}:5244/dav")
            self.assertTrue(mgr.is_local_target(), host)

    def test_remote_host_is_not_managed(self) -> None:
        """**核心回归点**：WebDAV 指向云服务器时绝不能去启动本地进程。

        将来把 OpenList 放到阿里云 ECS 上时，这条保证本程序不会
        在用户机器上白拉起一个用不上的实例。
        """
        for host in ("47.98.1.1", "openlist.example.com", "192.168.1.50"):
            mgr = self._mgr(f"http://{host}:5244/dav")
            self.assertFalse(mgr.is_local_target(), host)
            self.assertIn("不需要 OpenList", mgr.status().note)

    def test_port_parsing(self) -> None:
        self.assertEqual(self._mgr("http://127.0.0.1:6100/dav").port, 6100)
        self.assertEqual(self._mgr("http://127.0.0.1/dav").port, 5244)
        self.assertEqual(self._mgr("https://127.0.0.1/dav").port, 443)

        # 空地址：直接构造，绕开 helper 的"空闲端口"默认值
        cfg = UploadConfig(webdav_url="", openlist_dir="")
        mgr = OpenListManager(cfg, app_base=self.app_base, data_dir=self.data_dir)
        self.assertEqual(mgr.port, 5244)
        self.assertTrue(mgr.is_local_target(), "空地址按本机处理")

    def test_webdav_and_webui_urls(self) -> None:
        mgr = self._mgr("http://127.0.0.1:5244/dav")
        self.assertEqual(mgr.webui_url(), "http://127.0.0.1:5244/")
        self.assertEqual(mgr.webdav_url(), "http://127.0.0.1:5244/dav")

    def test_url_without_scheme_is_tolerated(self) -> None:
        mgr = self._mgr("127.0.0.1:5244/dav")
        self.assertTrue(mgr.is_local_target())
        self.assertEqual(mgr.port, 5244)


class InstallDirTest(_Base):
    def test_explicit_dir_wins(self) -> None:
        custom = self.root / "my-openlist"
        custom.mkdir()
        (custom / "openlist.exe").write_bytes(b"MZ")
        mgr = self._mgr(openlist_dir=str(custom))
        self.assertEqual(mgr.install_dir, custom)
        self.assertTrue(mgr.is_installed())

    def test_autodetect_next_to_app(self) -> None:
        expected = self._install_fake()
        mgr = self._mgr()
        self.assertEqual(mgr.install_dir, expected)
        self.assertTrue(mgr.is_installed())

    def test_not_installed_reports_hint(self) -> None:
        mgr = self._mgr()          # 指向空闲端口 → 未运行
        self.assertFalse(mgr.is_running())
        self.assertFalse(mgr.is_installed())
        self.assertIn("未找到", mgr.status().note)
        # 提示里要带上"应该放哪"，否则用户无从下手
        self.assertIn("openlist", mgr.status().note.lower())

    def test_candidates_cover_sibling_layout(self) -> None:
        """发布目录与 OpenList 并排是常见摆法，必须能认出来。"""
        candidates = openlist_candidates(self.app_base, self.data_dir)
        self.assertIn(self.app_base.parent / "OpenList", candidates)
        # 开发布局：项目根/dist/OpenList
        self.assertIn(self.app_base / "dist" / "OpenList", candidates)


class RunningDetectionTest(_Base):
    def test_port_probe_true_when_listening(self) -> None:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            mgr = self._mgr(f"http://127.0.0.1:{port}/dav")
            self.assertTrue(mgr.is_running())
        finally:
            srv.close()

    def test_port_probe_false_when_nothing_listening(self) -> None:
        # 绑定再立刻释放，拿到一个几乎肯定没人用的端口
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        mgr = self._mgr(f"http://127.0.0.1:{port}/dav")
        self.assertFalse(mgr.is_running())

    def test_status_reports_external_instance(self) -> None:
        """用户自己开的实例：状态要如实说明「本程序不会关闭它」。"""
        self._install_fake()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            mgr = self._mgr(f"http://127.0.0.1:{port}/dav")
            st = mgr.status()
            self.assertTrue(st.running)
            self.assertFalse(st.managed, "不是我们启动的，不该标记为 managed")
            self.assertIn("不会关闭", st.note)
        finally:
            srv.close()


class StartStopTest(_Base):
    def test_start_raises_when_not_installed(self) -> None:
        mgr = self._mgr()
        with self.assertRaises(QgbError) as ctx:
            mgr.start(wait=0.1)
        self.assertIn("openlist.exe", ctx.exception.message)

    def test_start_refuses_for_remote_target(self) -> None:
        """远端目标下启动本地进程是无意义的，必须直接拒绝。"""
        self._install_fake()
        mgr = self._mgr("http://47.98.1.1:5244/dav")
        with self.assertRaises(QgbError) as ctx:
            mgr.start(wait=0.1)
        self.assertIn("远端", ctx.exception.message)

    def test_ensure_running_never_raises(self) -> None:
        """网盘没起来不该阻止程序启动 —— 用户仍要能配置、看日志。"""
        mgr = self._mgr()
        ok, message = mgr.ensure_running()
        self.assertFalse(ok)
        self.assertIn("OpenList", message)

    def test_ensure_running_skips_remote(self) -> None:
        mgr = self._mgr("http://47.98.1.1:5244/dav")
        ok, message = mgr.ensure_running()
        self.assertTrue(ok, "远端目标视为「无需本地实例」，不算失败")
        self.assertIn("跳过", message)

    def test_stop_is_noop_when_not_ours(self) -> None:
        """**安全回归点**：没启动过就绝不能去杀进程。

        早先在 NapCat 那边踩过同类的坑：按进程名去杀会把无关进程
        （甚至本工具运行环境自带的 node）一起干掉。
        """
        mgr = self._mgr()
        mgr.stop()          # 不该抛异常，也不该做任何事
        self.assertIsNone(mgr._proc)
        self.assertFalse(mgr._started_by_us)

    def test_stop_after_our_process_exited(self) -> None:
        mgr = self._mgr()
        mgr._started_by_us = True     # 模拟曾经启动过，但进程已退出
        mgr._proc = None
        mgr.stop()
        self.assertFalse(mgr._started_by_us)


class LogTailTest(_Base):
    def test_log_tail_redacts(self) -> None:
        mgr = self._mgr()
        mgr.log_dir.mkdir(parents=True, exist_ok=True)
        (mgr.log_dir / "openlist.out.log").write_text(
            "INFO server started\nAuthorization: Bearer eyJhbGciOiJIUzI1NiJ9.SECRET\n",
            encoding="utf-8",
        )
        tail = mgr.last_log_tail()
        self.assertIn("server started", tail)
        self.assertNotIn("eyJhbGciOiJIUzI1NiJ9.SECRET", tail, "日志脱敏必须生效")

    def test_log_tail_empty_when_no_logs(self) -> None:
        self.assertEqual(self._mgr().last_log_tail(), "")


if __name__ == "__main__":
    unittest.main()
