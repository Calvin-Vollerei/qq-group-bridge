"""上传适配器测试。

WebDAV 部分用内存版假服务端，**离线验证生产主路径**（OpenList 中转）。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from qgb.config import UploadConfig
from qgb.dev.fakes import make_secrets
from qgb.dev.webdav_fake import FakeWebDAVServer
from qgb.errors import ConfigError, UploadError
from qgb.secrets import (
    KEY_NETDISK_WEBDAV_PASSWORD,
    KEY_NETDISK_WEBDAV_USERNAME,
    SecretStore,
)
from qgb.uploaders import available_adapters, build_uploader
from qgb.uploaders import local as local_mod
from qgb.uploaders.local import LocalUploader
from qgb.uploaders.webdav import WebDAVUploader

PAYLOAD = b"NETDISK-PAYLOAD\x00" * 500


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-up-")
        self.tmp = Path(self._tmp.name)
        self.secrets: SecretStore = make_secrets(self.tmp)
        self.local_file = self.tmp / "报告_v3.pdf"
        self.local_file.write_bytes(PAYLOAD)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# ------------------------------------------------------------------ 本地目录

class TestLocalUploader(BaseCase):
    def _cfg(self, root: Path | None = None, **kw) -> UploadConfig:
        return UploadConfig(
            adapter="local",
            remote_root=kw.pop("remote_root", "QQ群备份"),
            local_root=str(root or (self.tmp / "net")),
            split_by_group=kw.pop("split_by_group", True),
            verify_after_upload=kw.pop("verify_after_upload", True),
        )

    def test_report_and_target(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        self.assertEqual(up.name, "local")
        self.assertIn("本地目录", up.display_name)
        self.assertTrue(up.describe_target())

    def test_test_ok(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        ok, msg = up.test()
        self.assertTrue(ok, msg)

    def test_test_without_root(self) -> None:
        up = LocalUploader(UploadConfig(adapter="local", local_root=""), self.secrets)
        ok, msg = up.test()
        self.assertFalse(ok)
        self.assertIn("未设置", msg)

    def test_upload_creates_directory_and_content(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        remote = up.build_remote_path(group_id="123456789", filename="报告_v3.pdf")
        result = up.upload(self.local_file, remote)

        self.assertTrue(result.verified)
        self.assertEqual(result.size, len(PAYLOAD))
        # remote_path 是**远端路径**（服务端视角），不是本地绝对路径：
        # 与 WebDAV 等其它适配器保持一致，幂等/重试路径才不会把二者搞混。
        self.assertEqual(result.remote_path, remote)

        written = Path(self._cfg().local_root) / remote.lstrip("/")
        self.assertTrue(written.is_file())
        self.assertEqual(written.read_bytes(), PAYLOAD)
        self.assertIn("123456789", str(written))

    def test_upload_result_roundtrips_into_remote_size(self) -> None:
        """**回归测试**：``upload()`` 的返回值必须能被 ``remote_size()`` 直接用。

        真实教训：``upload()`` 曾返回本地**绝对**路径（``D:\\...\\x.pdf``），
        而 ``remote_size()`` 会把它当远端路径再拼一次 ``local_root``，
        于是 Windows 上永远查不到、返回 None（Linux 上 ``/tmp/...``
        加前缀后恰好仍是有效路径，所以只在 Windows 暴露）。
        上传后校验会因此永远失败。
        """
        up = LocalUploader(self._cfg(), self.secrets)
        result = up.upload(self.local_file, "/QQ群备份/x.pdf")

        self.assertEqual(up.remote_size(result.remote_path), len(PAYLOAD))
        self.assertIsNone(up.remote_size("/QQ群备份/不存在.pdf"))

    def test_absolute_path_outside_root_is_rejected(self) -> None:
        """根目录之外的绝对路径必须被拒（不能因为"是绝对路径"就放行）。"""
        up = LocalUploader(self._cfg(), self.secrets)
        with self.assertRaises(UploadError):
            up.remote_size(str(Path(tempfile.gettempdir()) / "definitely-outside.pdf"))

    def test_remote_path_leading_slash_is_relative_to_root(self) -> None:
        """**回归测试**：``/群目录/x.pdf`` 虽然以 ``/`` 开头，却是**相对** local_root 的。

        真实事故：``_resolve`` 里曾写 ``Path(raw).is_absolute() or drive`` 来识别
        「文件系统路径」。Windows 上 ``Path("/QQ群备份/x.pdf").is_absolute()`` 是
        ``False``（无盘符），Linux 上却是 ``True``（根就是 ``/``）——
        于是 Linux/CI 上每一条正常远端路径都被判成越界，**上传 100% 失败**：

            拒绝越界路径：x.pdf

        所以只能按**盘符**识别，这条测试在三个平台上都必须过。
        """
        up = LocalUploader(self._cfg(), self.secrets)
        root = Path(self._cfg().local_root).resolve()

        for remote in ("/QQ群备份/x.pdf", "/x.pdf", "x.pdf", "/a/b/c"):
            resolved = up._resolve(remote).resolve()
            self.assertTrue(
                resolved == root or root in resolved.parents,
                f"{remote} 被解析到根目录之外：{resolved}",
            )

    def test_drive_letter_path_inside_root_is_accepted(self) -> None:
        """带盘符且位于根目录内的路径（``upload()`` 的历史返回值）应被接受。"""
        up = LocalUploader(self._cfg(), self.secrets)
        root = Path(self._cfg().local_root)
        inside = up._resolve(str(root / "子目录" / "x.pdf"))
        self.assertEqual(inside, root / "子目录" / "x.pdf")

    def test_fix_does_not_rely_on_is_absolute_for_remote_paths(self) -> None:
        """**跨平台回归（源码级）**：远端路径的判定不许依赖 ``is_absolute()``。

        真实事故：``_resolve`` 里曾写

            if Path(raw).is_absolute() or Path(raw).drive:

        来识别「文件系统路径」。Windows 上 ``Path("/QQ群备份/x.pdf").is_absolute()``
        是 ``False``（无盘符），Linux 上却是 ``True``（根就是 ``/``）——
        于是 Linux/CI 上每一条正常远端路径都进了那个分支、被判成越界，
        **上传 100% 失败**（``拒绝越界路径：x.pdf``）。

        本机是 Windows，跑不出这个差异，所以这里做源码级断言：
        判定只能按**盘符**来。
        """
        import io
        import inspect
        import tokenize

        source = inspect.getsource(local_mod)

        # 用 tokenize 剥掉注释与字符串字面量：文档字符串里正好举例说明了
        # 「不能依赖 is_absolute()」，直接对全文做子串匹配会把它误判成调用。
        code_tokens = [
            tok.string
            for tok in tokenize.generate_tokens(io.StringIO(source).readline)
            if tok.type not in (tokenize.COMMENT, tokenize.STRING)
        ]
        code = " ".join(code_tokens)

        self.assertIn("_WINDOWS_DRIVE", code, "应按盘符判定")
        self.assertNotIn(
            "is_absolute", code,
            "代码里不允许调用 is_absolute()：它在 Windows/Linux 上语义不同",
        )

    def test_posix_absolute_remote_paths_are_still_relative_to_root(self) -> None:
        """**跨平台回归（行为级）**：注入「``/x`` 是绝对路径且无盘符」的语义。

        这是 Linux 的真实取值组合，也是修复前唯一会翻车的那一组：
        绝对路径 + 没有盘符。修复后判定只按盘符，所以这一组合必须正常。

        实现要点：把 ``local_mod.Path`` 换成一个**只服务这一分支**的假类
        （远端路径必须是「相对无盘符」的，因此假类要拒绝在根目录内解析）。
        """
        from unittest import mock

        up = LocalUploader(self._cfg(), self.secrets)
        root = Path(self._cfg().local_root).resolve()

        marker = "QQ群备份"

        class FakePath(str):
            """模拟「无盘符的绝对路径」—— 修复前会翻车的那一组取值。

            继承 ``str`` 是为了让 ``Path()`` / ``os.fspath`` / ``joinpath`` 都能直接用。
            """

            def is_absolute(self) -> bool:      # Linux 上 /x 是 True
                return True

            @property
            def drive(self) -> str:
                return ""

            def expanduser(self) -> "FakePath":
                return self

            def resolve(self) -> Path:
                # 故意解析到根目录外：若代码还在用 is_absolute()/drive 判定，
                # 就会走进「越界」分支并抛错 —— 正是 CI 上的症状
                return Path(str(self).lstrip("/") or marker) / "_fake"

        with mock.patch.object(local_mod, "Path", FakePath):
            for remote in ("/QQ群备份/x.pdf", "/x.pdf", "/a/b/c", "x.pdf"):
                resolved = Path(str(up._resolve(remote)))
                self.assertTrue(
                    resolved == root or root in resolved.resolve().parents,
                    f"POSIX 语义下 {remote} 没被解析到根目录内：{resolved}"
                    "（修复前这一组会被判成越界，正是 CI 上上传全挂的原因）",
                )
        # 穿越拦截不在这里测：假类改写了 resolve()，守卫那条路径不成立。
        # 它由 test_path_traversal_stays_inside_root 用真实 Path 覆盖。

    def test_split_by_group_off(self) -> None:
        """关闭分群时，文件名直接落在本地根目录下。

        **本地适配器不拼 remote_root**：``local_root`` 本身就是根，
        再叠一层 ``remote_root``（默认 ``/QQ群备份``）会得到
        ``D:\\目标\\QQ群备份\\a.pdf`` 这种多余嵌套。
        """
        up = LocalUploader(self._cfg(split_by_group=False), self.secrets)
        path = up.build_remote_path(group_id="999", filename="a.pdf")
        self.assertEqual(path, "/a.pdf")

    def test_local_ignores_remote_root(self) -> None:
        """**回归测试**：本地路径不能出现多余的一层。"""
        up = LocalUploader(self._cfg(split_by_group=True), self.secrets)
        path = up.build_remote_path(group_id="123456789", filename="报告.pdf")
        self.assertEqual(path, "/123456789/报告.pdf")
        self.assertNotIn("QQ群备份", path)

    def test_ensure_dir_idempotent(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        up.ensure_dir("/a/b/c")
        up.ensure_dir("/a/b/c")
        self.assertTrue((Path(self._cfg().local_root) / "a/b/c").is_dir())

    def test_path_traversal_stays_inside_root(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        root = Path(self._cfg().local_root).resolve()
        resolved = up._resolve("/../../../../Windows/System32/evil.exe").resolve()
        self.assertTrue(
            resolved == root or root in resolved.parents,
            f"越界了：{resolved}",
        )

    def test_no_part_leftover(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        up.upload(self.local_file, "/QQ群备份/x.pdf")
        leftovers = list(Path(self._cfg().local_root).rglob("*.part"))
        self.assertEqual(leftovers, [])

    def test_remote_size(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        result = up.upload(self.local_file, "/QQ群备份/x.pdf")
        self.assertEqual(up.remote_size(result.remote_path), len(PAYLOAD))
        self.assertIsNone(up.remote_size("/QQ群备份/不存在.pdf"))

    def test_missing_local_file_raises(self) -> None:
        up = LocalUploader(self._cfg(), self.secrets)
        with self.assertRaises(UploadError):
            up.upload(self.tmp / "不存在.pdf", "/QQ群备份/x.pdf")


# ------------------------------------------------------------------ WebDAV

class TestWebDAVUploader(BaseCase):
    def _uploader(self, srv: FakeWebDAVServer, **kw) -> WebDAVUploader:
        self.secrets.set(KEY_NETDISK_WEBDAV_USERNAME, srv.username)
        self.secrets.set(KEY_NETDISK_WEBDAV_PASSWORD, srv.password)
        cfg = UploadConfig(
            adapter="webdav",
            remote_root=kw.pop("remote_root", "QQ群备份"),
            webdav_url=srv.url,
            split_by_group=kw.pop("split_by_group", True),
            verify_after_upload=kw.pop("verify_after_upload", True),
        )
        return WebDAVUploader(cfg, self.secrets)

    def test_url_quoting_for_chinese(self) -> None:
        up = WebDAVUploader(UploadConfig(webdav_url="http://h/dav"), self.secrets)
        url = up._url("/QQ群备份/2026/报告 v3.pdf")
        self.assertTrue(url.startswith("http://h/dav/"))
        self.assertNotIn(" ", url)
        self.assertIn("%", url)

    def test_test_ok(self) -> None:
        with FakeWebDAVServer() as srv:
            ok, msg = self._uploader(srv).test()
            self.assertTrue(ok, msg)

    def test_test_bad_credentials(self) -> None:
        with FakeWebDAVServer() as srv:
            self.secrets.set(KEY_NETDISK_WEBDAV_USERNAME, "admin")
            self.secrets.set(KEY_NETDISK_WEBDAV_PASSWORD, "wrong-password")
            up = WebDAVUploader(
                UploadConfig(adapter="webdav", webdav_url=srv.url), self.secrets
            )
            ok, msg = up.test()
            self.assertFalse(ok)
            self.assertIn("认证失败", msg)

    def test_test_unreachable(self) -> None:
        up = WebDAVUploader(
            UploadConfig(adapter="webdav", webdav_url="http://127.0.0.1:1/dav"),
            self.secrets,
        )
        ok, msg = up.test()
        self.assertFalse(ok)
        self.assertIn("无法连接", msg)

    def test_test_without_url(self) -> None:
        up = WebDAVUploader(UploadConfig(adapter="webdav", webdav_url=""), self.secrets)
        ok, msg = up.test()
        self.assertFalse(ok)
        self.assertIn("未填写", msg)

    def test_ensure_dir_creates_each_level(self) -> None:
        with FakeWebDAVServer() as srv:
            up = self._uploader(srv)
            up.ensure_dir("/QQ群备份/123456789")
            self.assertTrue(srv.has_dir("QQ群备份"))
            self.assertTrue(srv.has_dir("QQ群备份/123456789"))

    def test_ensure_dir_skips_existing_mount_point(self) -> None:
        """**回归测试**：已存在的目录（OpenList 的挂载点）不能被当成错误。

        真实故障：OpenList 的 WebDAV 根下暴露的是各存储的挂载点（如 ``/baidu``），
        对挂载点发 MKCOL 会被拒（实测 **403**，而非规范里的 405）。
        早先的实现对每一段都无条件 MKCOL，于是任何上传都在
        「创建远端目录失败：/baidu 返回 403」处失败 —— 而网页端和 API 却正常，
        极难定位。
        """
        with FakeWebDAVServer(mkcol_existing_code=403) as srv:
            up = self._uploader(srv)
            # 先造出"挂载点"（模拟 OpenList 里已挂载的 /baidu）
            up.ensure_dir("/baidu")

            # 再往挂载点下面建目录：绝不能因为挂载点已存在而失败
            up.ensure_dir("/baidu/QQ群备份/123456789")

            self.assertTrue(srv.has_dir("baidu"))
            self.assertTrue(srv.has_dir("baidu/QQ群备份"))
            self.assertTrue(srv.has_dir("baidu/QQ群备份/123456789"))

    def test_ensure_dir_is_idempotent_with_403_server(self) -> None:
        """重复调用（目录已存在）也必须成功，不能报 403。"""
        with FakeWebDAVServer(mkcol_existing_code=403) as srv:
            up = self._uploader(srv)
            up.ensure_dir("/baidu/QQ群备份")
            up.ensure_dir("/baidu/QQ群备份")      # 第二次：已存在
            up.ensure_dir("/baidu/QQ群备份")      # 第三次
            self.assertTrue(srv.has_dir("baidu/QQ群备份"))

    def test_upload_writes_content_and_verifies(self) -> None:
        with FakeWebDAVServer() as srv:
            up = self._uploader(srv)
            remote = up.build_remote_path(group_id="123456789", filename="报告_v3.pdf")
            result = up.upload(self.local_file, remote)

            self.assertTrue(result.verified, "回读校验应通过")
            self.assertEqual(result.size, len(PAYLOAD))
            self.assertEqual(srv.file_at("QQ群备份/123456789/报告_v3.pdf"), PAYLOAD)

    def test_upload_autocreates_parent_dir(self) -> None:
        with FakeWebDAVServer() as srv:
            up = self._uploader(srv)
            up.upload(self.local_file, "/新建目录/子目录/x.pdf")
            self.assertIsNotNone(srv.file_at("新建目录/子目录/x.pdf"))

    def test_upload_bad_auth_raises_upload_error(self) -> None:
        with FakeWebDAVServer() as srv:
            self.secrets.set(KEY_NETDISK_WEBDAV_USERNAME, "admin")
            self.secrets.set(KEY_NETDISK_WEBDAV_PASSWORD, "bad")
            up = WebDAVUploader(
                UploadConfig(adapter="webdav", webdav_url=srv.url), self.secrets
            )
            with self.assertRaises(UploadError) as ctx:
                up.upload(self.local_file, "/QQ群备份/x.pdf")
            self.assertIn("认证失败", str(ctx.exception))

    def test_verify_detects_size_mismatch(self) -> None:
        """远端被截断时必须报错，而不是当作成功。"""
        with FakeWebDAVServer() as srv:
            up = self._uploader(srv)
            original = up.remote_size

            def lying(remote_path: str) -> int | None:  # 模拟远端内容不完整
                return 12345

            up.remote_size = lying  # type: ignore[assignment]
            with self.assertRaises(UploadError) as ctx:
                up.upload(self.local_file, "/QQ群备份/x.pdf")
            self.assertIn("校验失败", str(ctx.exception))
            up.remote_size = original  # type: ignore[assignment]

    def test_remote_size_none_when_missing(self) -> None:
        with FakeWebDAVServer() as srv:
            self.assertIsNone(self._uploader(srv).remote_size("/不存在.pdf"))

    def test_missing_local_file(self) -> None:
        with FakeWebDAVServer() as srv:
            up = self._uploader(srv)
            with self.assertRaises(UploadError):
                up.upload(self.tmp / "无.pdf", "/QQ群备份/x.pdf")


# ------------------------------------------------------------------ 注册表

class TestRegistry(BaseCase):
    def test_available_adapters(self) -> None:
        ids = {a[0] for a in available_adapters()}
        self.assertIn("webdav", ids)
        self.assertIn("local", ids)

    def test_build_uploader(self) -> None:
        up = build_uploader(UploadConfig(adapter="local", local_root=str(self.tmp)), self.secrets)
        self.assertIsInstance(up, LocalUploader)

    def test_build_unknown_adapter_raises(self) -> None:
        with self.assertRaises(ConfigError):
            build_uploader(UploadConfig(adapter="nope"), self.secrets)


if __name__ == "__main__":
    unittest.main()
