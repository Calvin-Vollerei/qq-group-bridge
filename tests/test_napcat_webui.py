"""NapCat WebUI 接口测试（用假服务端，**离线**验证鉴权与二维码流程）。

这些断言对应 NapCat v4.18.x 的真实路由：
    POST /api/auth/login
    POST /api/QQLogin/GetQQLoginQrcode
    POST /api/QQLogin/CheckLoginStatus
"""

from __future__ import annotations

import time
import unittest

from qgb.dev.napcat_fake import DEFAULT_QR_URL, FakeNapCat
from qgb.napcat.client import OneBotClient
from qgb.napcat.qr import (
    _password_hash,
    check_login,
    fetch_qrcode,
    generate_qr_png,
    webui_url,
)


class PasswordHashTest(unittest.TestCase):
    """登录哈希算法必须与 NapCat 完全一致，否则鉴权静默失败。

    算法来自 napcat-webui-backend/src/helper/SignToken.ts::

        generatePasswordHash(p) = sha256(p + ".napcat").hexdigest()

    并且 api/Auth.ts 读取的字段名是 ``hash``（不是 ``token``）。
    这两点都是真机踩出来的：发 ``{"token": ...}`` 会被回一句
    误导性的 ``token is empty``。
    """

    def test_matches_napcat_algorithm(self) -> None:
        import hashlib

        for token in ("aa14b7f9", "hello", "x" * 48, "令牌"):
            expected = hashlib.sha256(f"{token}.napcat".encode("utf-8")).hexdigest()
            self.assertEqual(_password_hash(token), expected)

    def test_known_vector(self) -> None:
        """固定向量：算法一旦被改动，这里立刻失败。"""
        import hashlib

        self.assertEqual(
            _password_hash("test"),
            hashlib.sha256(b"test.napcat").hexdigest(),
        )
        self.assertNotEqual(_password_hash("test"), _password_hash("test2"))

    def test_hash_is_not_the_raw_token(self) -> None:
        """绝不能把明文令牌当哈希发出去。"""
        token = "aa14b7f971f5a378650fdd0a9620cc6855f3c14d426d8dd6"
        self.assertNotIn(token, _password_hash(token))
        self.assertEqual(len(_password_hash(token)), 64)


class QrRenderTest(unittest.TestCase):
    def test_generate_png_is_valid_png(self) -> None:
        png = generate_qr_png("https://example.org/login?k=abc")
        self.assertIsNotNone(png, "未安装 segno 时应返回 None（此处应已安装）")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"), "不是合法 PNG")

    def test_generate_png_empty_input(self) -> None:
        self.assertIsNone(generate_qr_png(""))

    def test_webui_url(self) -> None:
        self.assertEqual(webui_url("http://127.0.0.1:6099/", "tok"),
                         "http://127.0.0.1:6099/webui?token=tok")
        self.assertEqual(webui_url("http://127.0.0.1:6099"), "http://127.0.0.1:6099/webui")
        self.assertEqual(webui_url(""), "")


class FetchQrcodeTest(unittest.TestCase):
    def test_happy_path_renders_local_png(self) -> None:
        with FakeNapCat() as nap:
            qr = fetch_qrcode(nap.base, nap.token)

        self.assertTrue(qr.ok, f"应取到二维码：{qr.error} / {qr.hint}")
        self.assertEqual(qr.qrcode_url, DEFAULT_QR_URL)
        self.assertEqual(qr.source, "webui+segno")
        self.assertTrue(qr.png_bytes.startswith(b"\x89PNG"))

    def test_wrong_token_reports_actionable_hint(self) -> None:
        with FakeNapCat() as nap:
            qr = fetch_qrcode(nap.base, "WRONG-TOKEN")
        self.assertFalse(qr.ok)
        self.assertIn("令牌", qr.error)
        self.assertTrue(qr.hint, "必须给出可操作的建议")

    def test_empty_token_reports_hint(self) -> None:
        with FakeNapCat() as nap:
            qr = fetch_qrcode(nap.base, "")
        self.assertFalse(qr.ok)
        self.assertTrue(qr.error)

    def test_qr_not_ready_yet(self) -> None:
        """组件刚起来、还没生成二维码时，应给出「稍等再试」而不是崩。"""
        with FakeNapCat(qr_ready=False) as nap:
            qr = fetch_qrcode(nap.base, nap.token)
        self.assertFalse(qr.ok)
        self.assertIn("二维码", qr.error + qr.hint)

    def test_already_logged_in(self) -> None:
        with FakeNapCat(logged_in=True) as nap:
            qr = fetch_qrcode(nap.base, nap.token)
        self.assertFalse(qr.ok)
        self.assertTrue(qr.error)

    def test_unreachable_port(self) -> None:
        qr = fetch_qrcode("http://127.0.0.1:1", "tok")
        self.assertFalse(qr.ok)
        self.assertIn("无法连接", qr.error)
        self.assertTrue(qr.hint)

    def test_no_base_url(self) -> None:
        qr = fetch_qrcode("", "tok")
        self.assertFalse(qr.ok)
        self.assertIn("未配置", qr.error)

    def test_naive_server_without_auth_also_works(self) -> None:
        """用户把鉴权关掉时（require_auth=False），也要能取到二维码。"""
        with FakeNapCat(require_auth=False) as nap:
            qr = fetch_qrcode(nap.base, "")
        self.assertTrue(qr.ok, f"{qr.error} / {qr.hint}")

    def test_save_png(self) -> None:
        import tempfile
        from pathlib import Path

        with FakeNapCat() as nap:
            qr = fetch_qrcode(nap.base, nap.token)
        self.assertTrue(qr.ok)
        with tempfile.TemporaryDirectory() as tmp:
            out = qr.save(Path(tmp) / "qr.png")
            self.assertIsNotNone(out)
            self.assertTrue(out.is_file())
            self.assertTrue(out.read_bytes().startswith(b"\x89PNG"))


class CheckLoginTest(unittest.TestCase):
    def test_not_logged_in_yet(self) -> None:
        with FakeNapCat() as nap:
            st = check_login(nap.base, nap.token)
        self.assertTrue(st.reachable)
        self.assertFalse(st.is_login)
        self.assertEqual(st.category, "offline")
        self.assertIn("未登录", st.message)

    def test_logged_in(self) -> None:
        with FakeNapCat(logged_in=True) as nap:
            st = check_login(nap.base, nap.token)
        self.assertTrue(st.is_login)
        self.assertEqual(st.category, "online")
        self.assertIn("已登录", st.message)

    def test_offline_after_being_logged_in(self) -> None:
        """掉线要能被识别出来，界面才能提示「重新扫码」。

        注意 NapCat 的语义：``isLogin`` 要求 ``isOnline === true``，
        掉线时报 ``isLogin=false, isOffline=true`` —— 两者**互斥**。
        """
        with FakeNapCat(offline=True) as nap:
            st = check_login(nap.base, nap.token)
        self.assertFalse(st.is_login)
        self.assertTrue(st.is_offline)
        self.assertEqual(st.category, "expired")
        self.assertIn("失效", st.message)

    def test_unreachable_is_not_reported_as_bad_token(self) -> None:
        """连不上 ≠ 令牌错。混为一谈会把用户引向错误的排查方向。"""
        st = check_login("http://127.0.0.1:1", "some-token")
        self.assertIn("无法连接", st.message)
        self.assertNotIn("令牌", st.message)

    def test_wrong_token(self) -> None:
        with FakeNapCat() as nap:
            st = check_login(nap.base, "WRONG")
        self.assertFalse(st.reachable)
        self.assertIn("令牌", st.message)

    def test_unreachable(self) -> None:
        st = check_login("http://127.0.0.1:1", "tok")
        self.assertFalse(st.reachable)
        self.assertEqual(st.category, "unknown")


class OneBotClientTest(unittest.TestCase):
    """同一个假服务端也实现了 OneBot 侧动作，顺带验证客户端。"""

    def test_get_login_info_when_logged_in(self) -> None:
        with FakeNapCat(logged_in=True) as nap:
            info = OneBotClient(nap.base).get_login_info()
        self.assertTrue(info.online)
        self.assertEqual(info.category, "online")

    def test_get_login_info_when_failed(self) -> None:
        """OneBot 单独无法区分「组件没起来」和「还没扫码」——这正是
        门面层优先走 WebUI ``CheckLoginStatus`` 的原因。这里只断言
        「没有谎报已登录」。
        """
        with FakeNapCat(logged_in=False) as nap:
            info = OneBotClient(nap.base).get_login_info()
        self.assertFalse(info.online)
        self.assertIn(info.category, ("offline", "unknown"))

    def test_group_file_list_roundtrip(self) -> None:
        files = [{
            "group_id": 123456, "file_id": "f1", "file_name": "报告.pdf",
            "busid": 102, "file_size": 1234, "upload_time": 1700000000,
            "uploader_name": "张三",
        }]
        with FakeNapCat(logged_in=True, files=files) as nap:
            got, folders = OneBotClient(nap.base).get_group_file_list("123456")

        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].name, "报告.pdf")
        self.assertEqual(got[0].size, 1234)
        self.assertEqual(folders, [])

    def test_group_file_url(self) -> None:
        with FakeNapCat(logged_in=True,
                        file_urls={"f1": "http://127.0.0.1:9/f1.pdf"}) as nap:
            url = OneBotClient(nap.base).get_group_file_url("123456", "f1")
        self.assertIn("f1.pdf", url)

    # -------------------------------------------------- 接口名契约

    def test_uses_real_action_names(self) -> None:
        """**回归测试**：NapCat 没有 ``get_group_file_list``。

        真机上调用它会返回 ``不支持的Api get_group_file_list``。
        正确名字是 ``get_group_root_files`` / ``get_group_files_by_folder``。
        假服务端对错误名字会明确失败，所以这条测试能守住这个契约。
        """
        import requests

        files = [{"group_id": 1, "file_id": "f1", "file_name": "a.pdf",
                  "size": 100, "busid": 102, "uploader_name": "张三"}]
        with FakeNapCat(logged_in=True, files=files) as nap:
            client = OneBotClient(nap.base)

            # 正确名字能用
            got, folders = client.get_group_file_list("1")
            self.assertEqual(len(got), 1)
            self.assertEqual(got[0].name, "a.pdf")
            self.assertEqual(got[0].size, 100)
            self.assertEqual(folders, [])

            # 错误名字在真机上就是失败的 —— 假服务端也要如实模拟
            wrong = requests.post(f"{nap.base}/get_group_file_list", json={"group_id": 1},
                                  timeout=5).json()
            self.assertEqual(wrong.get("status"), "failed")
            self.assertIn("不支持的Api", wrong.get("message", ""))

    def test_root_files_and_folder_files_use_different_actions(self) -> None:
        """根目录与子文件夹走的是两个不同接口，不能混用。"""
        root_files = [{"group_id": 1, "file_id": "r1", "file_name": "root.pdf",
                       "size": 10, "busid": 102}]
        inner = [{"group_id": 1, "file_id": "i1", "file_name": "inner.pdf",
                  "size": 20, "busid": 102}]
        folders = [{"group_id": 1, "folder_id": "/abc", "folder_name": "资料"}]

        with FakeNapCat(logged_in=True, files=root_files, folders=folders,
                        folder_files={"/abc": inner}) as nap:
            client = OneBotClient(nap.base)

            got, got_folders = client.get_group_file_list("1")
            self.assertEqual([f.name for f in got], ["root.pdf"])
            self.assertEqual(len(got_folders), 1)

            inner_got, _ = client.get_group_file_list("1", folder_id="/abc")
            self.assertEqual([f.name for f in inner_got], ["inner.pdf"])

    def test_pagination_terminates_when_server_ignores_it(self) -> None:
        """**回归测试**：服务端不支持分页时不能无限递归。

        ``get_group_root_files`` 没有 start 参数，每轮都返回同一批。
        曾经照着"可能会分页"的假设写了翻页递归，结果**无限递归、调用卡死**
        （热检查时真实踩到）。现在明确不分页，必须能立刻返回。
        """
        many = [{"group_id": 1, "file_id": f"f{i}", "file_name": f"{i}.dat",
                 "size": 1, "busid": 102} for i in range(60)]

        with FakeNapCat(logged_in=True, files=many) as nap:
            client = OneBotClient(nap.base)
            start = time.monotonic()
            got, _folders = client.get_group_file_list("1", count=50)
            elapsed = time.monotonic() - start

        self.assertEqual(len(got), 60, "一次调用就该拿到全部文件")
        self.assertLess(elapsed, 10, "不该卡死")
        self.assertEqual(len({f.file_id for f in got}), 60, "结果需去重")

    def test_duplicate_entries_are_deduplicated(self) -> None:
        """服务端偶尔重复返回同一条，不能变成重复搬运。"""
        dup = [{"group_id": 1, "file_id": "same", "file_name": "a.dat",
                "size": 1, "busid": 102}] * 3
        with FakeNapCat(logged_in=True, files=dup) as nap:
            got, _ = OneBotClient(nap.base).get_group_file_list("1")
        self.assertEqual(len(got), 1)

    # -------------------------------------------------- 历史文件（file_count）

    def test_client_requests_large_file_count(self) -> None:
        """**回归测试**：必须显式传 file_count，否则历史文件被静默丢弃。

        真机实测：``get_group_root_files`` 不传 file_count 只返回约 40-50 条；
        传了之后返回量随之上调（上限约 422）。假服务端如实模拟了这个截断，
        所以这条测试能真正守住它 —— 某群声明 505 个文件，
        根 422 + 子目录 83 = 505 才算完整。
        """
        many = [{"group_id": 1, "file_id": f"f{i}", "file_name": f"{i}.dat",
                 "size": 1, "busid": 102} for i in range(300)]

        with FakeNapCat(logged_in=True, files=many) as nap:
            got, _ = OneBotClient(nap.base).get_group_file_list("1")

        self.assertEqual(
            len(got), 300,
            "只拿到 50 个说明没传 file_count —— 历史文件会被静默丢掉",
        )

    def test_file_count_is_configurable(self) -> None:
        many = [{"group_id": 1, "file_id": f"f{i}", "file_name": f"{i}.dat",
                 "size": 1, "busid": 102} for i in range(120)]

        with FakeNapCat(logged_in=True, files=many) as nap:
            client = OneBotClient(nap.base, file_count=60)
            got, _ = client.get_group_file_list("1")
        self.assertEqual(len(got), 60, "应尊重调用方设定的 file_count")

    def test_folder_listing_also_passes_file_count(self) -> None:
        """子目录同样需要 file_count，否则大目录会被截断。"""
        inner = [{"group_id": 1, "file_id": f"i{i}", "file_name": f"{i}.pdf",
                  "size": 1, "busid": 102} for i in range(200)]
        with FakeNapCat(logged_in=True, folders=[{"group_id": 1, "folder_id": "/big",
                                                  "folder_name": "大目录"}],
                        folder_files={"/big": inner}) as nap:
            got, _ = OneBotClient(nap.base).get_group_file_list("1", folder_id="/big")
        self.assertEqual(len(got), 200)

    def test_walk_does_not_duplicate_files(self) -> None:
        """**回归测试**：遍历不能因为"翻页"把同一批文件重复产出。

        真实故障：``walk_group_files`` 曾有 ``for page in range(20)`` 的翻页循环，
        但服务端忽略 ``start``、每轮返回同一批，于是同一批被累加 20 遍
        —— 实测某群声明 1103 个文件却"遍历到" 19982 个（约 20 倍）。
        """
        root_files = [{"group_id": 1, "file_id": f"r{i}", "file_name": f"r{i}.dat",
                       "size": 1, "busid": 102} for i in range(80)]
        inner = [{"group_id": 1, "file_id": f"i{i}", "file_name": f"i{i}.dat",
                  "size": 1, "busid": 102} for i in range(30)]
        folders = [{"group_id": 1, "folder_id": "/sub", "folder_name": "子目录"}]

        with FakeNapCat(logged_in=True, files=root_files, folders=folders,
                        folder_files={"/sub": inner}) as nap:
            walked = list(OneBotClient(nap.base).walk_group_files("1"))

        self.assertEqual(len(walked), 110, "应是 80 + 30，不能重复累加")
        self.assertEqual(len({f.file_id for f in walked}), 110, "不能有重复项")

    def test_walk_survives_duplicate_ids_across_folders(self) -> None:
        """跨目录出现同一 file_id 时也去重（安全网）。"""
        shared = [{"group_id": 1, "file_id": "dup", "file_name": "x.dat",
                   "size": 1, "busid": 102}]
        folders = [{"group_id": 1, "folder_id": "/a", "folder_name": "A"},
                   {"group_id": 1, "folder_id": "/b", "folder_name": "B"}]
        with FakeNapCat(logged_in=True, files=shared, folders=folders,
                        folder_files={"/a": shared, "/b": shared}) as nap:
            walked = list(OneBotClient(nap.base).walk_group_files("1"))
        self.assertEqual(len(walked), 1)

    def test_packet_backend_error_is_not_masked_by_fallback(self) -> None:
        """**回归测试**：真因不能被"备用接口名不存在"掩盖。

        真机现象：``get_group_file_url`` 返回
        ``packetBackend不可用…``（这是可操作的线索，指向 QQ 版本不匹配），
        而备用名 ``get_group_file_download_url`` 只回 ``不支持的Api``。
        早先的实现报的是**最后一条**错误，于是用户看到
        「不支持的Api get_group_file_download_url」——完全指向错误方向。
        """
        from qgb.errors import NapCatError

        msg = ("packetBackend不可用，请参照文档检查packetBackend状态！"
               "错误堆栈信息：[Core] [Packet] ...")
        with FakeNapCat(logged_in=True, url_error=msg) as nap:
            client = OneBotClient(nap.base)
            with self.assertRaises(NapCatError) as ctx:
                client.get_group_file_url("1", "f1", 102)

        error = ctx.exception
        self.assertIn("PacketBackend", error.message)
        self.assertNotIn("get_group_file_download_url", error.message)
        # 必须给出可操作的方向，而不是让人去猜
        self.assertIn("自带运行时", error.hint)

    def test_generic_failure_reports_first_informative_error(self) -> None:
        """非 PacketBackend 的失败也要挑信息量最大的那条。"""
        from qgb.errors import NapCatError

        with FakeNapCat(logged_in=True,
                        url_error="file not found or expired") as nap:
            client = OneBotClient(nap.base)
            with self.assertRaises(NapCatError) as ctx:
                client.get_group_file_url("1", "f1", 102)
        self.assertIn("file not found", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
