"""门面层（AppController）无头测试 —— GUI 的业务逻辑靠这层验证。"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qgb import paths
from qgb.controller import AppController, state_counts_summary
from qgb.logging_setup import reset_logging_for_tests
from qgb.models import GroupFile, TransferState


class ControllerCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-ctrl-")
        self.tmp = Path(self._tmp.name)

        self._env = mock.patch.dict(
            os.environ,
            {"QGB_DATA_DIR": str(self.tmp / "data")},
            clear=False,
        )
        self._env.start()
        paths.reset_cache()
        reset_logging_for_tests()

        self.controller = AppController(
            config_path=self.tmp / "config.json",
            log_dir=self.tmp / "logs",
        )
        self.controller.load()

    # -------------------------------------------------- 事件投递

    def test_post_tolerates_message_key_in_data(self) -> None:
        """``_post(kind, msg, **state)`` 里 state 带 message 键时**不能崩**。

        真实教训：原先 ``message`` 是普通参数，``**state``（state 里恰好有
        message）会触发 ``got multiple values for argument 'message'``，
        导致「检查登录状态」这个后台任务**每次调用都失败** ——
        日志刷满 TypeError，而界面上只表现为「点了没反应」。
        """
        self.controller.drain()          # 清掉 load() 期间的初始化事件

        self.controller._post(
            "login", "QQ 状态：已登录",
            **{"category": "online", "message": "已登录", "source": "webui"},
        )
        events = self.controller.drain()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "login")
        self.assertEqual(events[0].message, "QQ 状态：已登录")
        # state 里的 message 完好保留在 data 里，不丢信息
        self.assertEqual(events[0].data["message"], "已登录")
        self.assertEqual(events[0].data["category"], "online")

    def test_post_still_accepts_level_keyword(self) -> None:
        """修好 message 冲突后，``level=`` 的关键字用法不能被破坏。"""
        self.controller.drain()

        self.controller._post("qr", "二维码失败", level="warning", ok=False, hint="稍后重试")
        event = self.controller.drain()[0]
        self.assertEqual(event.level, "warning")
        self.assertEqual(event.message, "二维码失败")
        self.assertFalse(event.data["ok"])
        self.assertEqual(event.data["hint"], "稍后重试")

    def test_post_default_positional_message(self) -> None:
        self.controller.drain()

        self.controller._post("info", "纯文本")
        event = self.controller.drain()[0]
        self.assertEqual(event.message, "纯文本")
        self.assertEqual(event.data, {})

    # -------------------------------------------------- WebUI 令牌解析

    def test_webui_token_prefers_component_config(self) -> None:
        """**回归测试**：令牌必须以组件配置为准，而不能让旧缓存赖着不走。

        真实故障：切换 QQ 组件运行方式（挂钩已装 QQ ↔ 自带运行时）后，
        NapCat 会重新生成令牌，而凭据库里还是旧值。程序继续用旧令牌，
        就会一直报「WebUI 令牌无效」，反复重试还触发 ``login rate limit`` ——
        症状很有迷惑性：配置文件明明是对的，程序就是不认。
        """
        from qgb.secrets import KEY_NAPCAT_WEBUI_TOKEN

        napcat = self.controller.napcat
        self.assertIsNotNone(napcat)

        # 先塞一个"旧令牌"进凭据库
        self.controller.secrets.set(KEY_NAPCAT_WEBUI_TOKEN, "STALE-TOKEN-0000")

        # 组件配置里是"新令牌"
        cfg_dir = napcat.config_root() / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "webui.json").write_text(
            '{"host":"127.0.0.1","port":6099,"token":"FRESH-TOKEN-9999","loginRate":10}',
            encoding="utf-8",
        )

        resolved = self.controller._webui_token()
        self.assertEqual(resolved, "FRESH-TOKEN-9999", "必须用组件配置里的令牌")
        self.assertEqual(
            self.controller.secrets.get(KEY_NAPCAT_WEBUI_TOKEN),
            "FRESH-TOKEN-9999",
            "发现不一致就该顺手把凭据库更新掉",
        )

    def test_webui_token_falls_back_to_store(self) -> None:
        """组件配置读不到时，退回凭据库里的值（别把已保存的令牌弄丢）。"""
        from qgb.secrets import KEY_NAPCAT_WEBUI_TOKEN

        self.controller.secrets.set(KEY_NAPCAT_WEBUI_TOKEN, "ONLY-IN-STORE")
        self.assertEqual(self.controller._webui_token(), "ONLY-IN-STORE")

    def test_webui_token_empty_when_nothing_available(self) -> None:
        self.assertEqual(self.controller._webui_token(), "")

    def tearDown(self) -> None:
        try:
            self.controller.shutdown()
        except Exception:
            pass
        reset_logging_for_tests()
        self._env.stop()
        paths.reset_cache()
        self._tmp.cleanup()

    # -------------------------------------------------- 加载

    def test_load_succeeds_without_crashing(self) -> None:
        self.assertIsNotNone(self.controller.config)
        # 即便凭据库/状态库不可用，界面也必须能起来
        health = self.controller.health()
        self.assertIn("data_dir", health)
        self.assertIn("credentials", health)

    def test_data_dir_is_inside_override(self) -> None:
        """数据目录必须在 ``QGB_DATA_DIR`` 之下。

        ⚠️ 不要用字符串前缀比较：Windows 的临时目录可能是**短名**形式
        （``C:\\Users\\RUNNER~1\\AppData\\Local\\Temp``），而解析后的路径是
        长名（``C:\\Users\\runneradmin\\...``），两者指向同一个目录却前缀不匹配。
        本机碰巧一致，CI 上就翻车了 —— 所以统一先 ``resolve()``。
        """
        data_dir = Path(self.controller.data_dir).resolve()
        expected = Path(self.tmp).resolve()
        self.assertTrue(
            data_dir == expected or expected in data_dir.parents,
            f"数据目录未使用 QGB_DATA_DIR：{data_dir}（期望位于 {expected} 之下）",
        )

    def test_paths_are_exposed(self) -> None:
        for key in ("data", "logs", "temp", "config"):
            self.assertTrue(self.controller.open_path(key), f"{key} 路径为空")

    def test_health_has_no_profile_fields(self) -> None:
        import json

        blob = json.dumps(self.controller.health(), ensure_ascii=False, default=str)
        for forbidden in ("nickname", "vip", "membership", "quota", "phone", "avatar"):
            self.assertNotIn(forbidden, blob)

    # -------------------------------------------------- 配置

    def test_validation_reports_missing_groups(self) -> None:
        problems = self.controller.validate()
        self.assertTrue(any("群" in p for p in problems))

    def test_save_and_reload(self) -> None:
        self.controller.config.groups = ["123456789"]
        self.controller.config.monitor.poll_interval_sec = 240
        problems = self.controller.save_settings()
        self.assertEqual(problems, [], f"配置应通过校验：{problems}")

        reloaded = AppController(
            config_path=self.tmp / "config.json", log_dir=self.tmp / "logs"
        )
        reloaded.load()
        try:
            self.assertEqual(reloaded.config.groups, ["123456789"])
            self.assertEqual(reloaded.config.monitor.poll_interval_sec, 240)
        finally:
            reloaded.shutdown()

    def test_save_config_never_contains_credentials(self) -> None:
        self.controller.config.groups = ["123456789"]
        self.controller.save_settings()
        text = (self.tmp / "config.json").read_text(encoding="utf-8")
        self.assertNotIn("password", text.lower())
        self.assertNotIn("token", text.lower())

    # -------------------------------------------------- 凭据

    def test_credentials_overview_and_clear(self) -> None:
        if self.controller.secrets is None:
            self.skipTest("凭据库不可用")

        self.controller.save_webdav_credentials("admin", "SuperSecretValue123")
        rows = self.controller.credentials_overview()
        self.assertTrue(rows)
        self.assertNotIn("SuperSecretValue123", str(rows))

        self.controller.clear_webdav_credentials()
        self.assertEqual(self.controller.credentials_overview(), [])

    def test_clear_all_credentials(self) -> None:
        if self.controller.secrets is None:
            self.skipTest("凭据库不可用")
        self.controller.save_webdav_credentials("admin", "AnotherSecret123")
        self.controller.clear_all_credentials()
        self.assertEqual(self.controller.credentials_overview(), [])

    # -------------------------------------------------- 监控

    def test_start_monitor_requires_valid_config(self) -> None:
        self.assertFalse(self.controller.start_monitor(), "无群号时不应启动")
        messages = [e.message for e in self.controller.drain()]
        self.assertTrue(any("配置" in m for m in messages))

    def test_start_monitor_with_valid_config(self) -> None:
        if self.controller.store is None:
            self.skipTest("状态库不可用")
        self.controller.config.groups = ["123456789"]
        self.controller.config.upload.adapter = "local"
        self.controller.config.upload.local_root = str(self.tmp / "net")

        self.assertTrue(self.controller.start_monitor())
        self.assertEqual(self.controller.monitor_state(), "running")

        self.controller.pause_monitor()
        self.assertEqual(self.controller.monitor_state(), "paused")
        self.controller.resume_monitor()
        self.assertEqual(self.controller.monitor_state(), "running")

        self.controller.stop_monitor()
        self.assertEqual(self.controller.monitor_state(), "stopped")

    def test_double_start_is_idempotent(self) -> None:
        if self.controller.store is None:
            self.skipTest("状态库不可用")
        self.controller.config.groups = ["123456789"]
        self.controller.config.upload.adapter = "local"
        self.controller.config.upload.local_root = str(self.tmp / "net")

        self.assertTrue(self.controller.start_monitor())
        self.assertFalse(self.controller.start_monitor(), "重复启动应返回 False")
        self.controller.stop_monitor()

    # -------------------------------------------------- 事件

    def test_events_are_queued_and_drainable(self) -> None:
        events = self.controller.drain()
        self.assertTrue(events, "加载阶段应产生事件")
        self.assertTrue(any(e.kind == "ready" for e in events))
        self.assertEqual(self.controller.drain(), [], "取走后队列应为空")

    def test_stats_shape(self) -> None:
        stats = self.controller.stats()
        for key in ("state", "seen", "counts", "bytes_text", "uptime"):
            self.assertIn(key, stats)
        self.assertIn(stats["state"], ("running", "paused", "stopped"))

    # -------------------------------------------------- NapCat

    def test_napcat_status_shape(self) -> None:
        status = self.controller.napcat_status()
        for key in ("installed", "running", "install_dir", "note"):
            self.assertIn(key, status)

    def test_install_hint_mentions_official_releases(self) -> None:
        hint = self.controller.napcat_install_hint()
        self.assertIn("NapCat", hint)
        self.assertIn("github.com", hint)

    def test_qq_login_state_degrades_when_unreachable(self) -> None:
        """NapCat 没起时必须返回 unknown，而不是抛异常。"""
        self.controller.config.napcat.api_base = "http://127.0.0.1:1"
        state = self.controller.qq_login_state()
        self.assertEqual(state["category"], "unknown")
        self.assertTrue(state["message"])

    # -------------------------------------------------- 网盘

    def test_adapter_choices(self) -> None:
        ids = {cid for cid, _ in self.controller.adapter_choices()}
        self.assertIn("webdav", ids)
        self.assertIn("local", ids)

    def test_test_netdisk_local_adapter(self) -> None:
        self.controller.config.upload.adapter = "local"
        self.controller.config.upload.local_root = str(self.tmp / "net")
        self.assertTrue(self.controller.test_netdisk())

    def test_requeue_failed(self) -> None:
        if self.controller.store is None:
            self.skipTest("状态库不可用")
        store = self.controller.store
        gf = GroupFile(group_id="1", file_id="x", name="a.pdf", busid=102)
        store.claim(gf)
        store.mark(gf.key, TransferState.FAILED)
        self.assertEqual(self.controller.requeue_failed(), 1)


class TestSummary(unittest.TestCase):
    def test_state_counts_summary(self) -> None:
        text = state_counts_summary({
            TransferState.DONE.value: 3,
            TransferState.FAILED.value: 1,
        })
        self.assertIn("已完成 3", text)
        self.assertIn("失败 1", text)

    def test_state_counts_summary_empty(self) -> None:
        self.assertEqual(state_counts_summary({}), "暂无记录")


if __name__ == "__main__":
    unittest.main()
