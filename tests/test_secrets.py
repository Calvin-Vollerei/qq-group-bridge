"""凭据库测试（用假后端，不依赖 Windows DPAPI）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from qgb.errors import CredentialError
from qgb.redact import clear_registered, redact_text
from qgb.secrets import (
    KEY_NETDISK_WEBDAV_PASSWORD,
    KEY_NETDISK_WEBDAV_USERNAME,
    InsecureDevBackend,
    SecretStore,
)


class TestSecretStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="qgb-secrets-")
        self.tmp = Path(self._tmp.name)
        self.path = self.tmp / "secrets.enc"
        self.store = SecretStore(self.path, backend=InsecureDevBackend())

    def tearDown(self) -> None:
        clear_registered()
        self._tmp.cleanup()

    # -------------------------------------------------- 基本读写

    def test_missing_file_is_empty_not_error(self) -> None:
        self.assertEqual(self.store.keys(), [])
        self.assertIsNone(self.store.get("nope"))

    def test_set_get_roundtrip(self) -> None:
        self.store.set("k1", "v1")
        self.assertEqual(self.store.get("k1"), "v1")

    def test_persistence_across_instances(self) -> None:
        self.store.set(KEY_NETDISK_WEBDAV_PASSWORD, "s3cr3t-value")
        other = SecretStore(self.path, backend=InsecureDevBackend())
        self.assertEqual(other.get(KEY_NETDISK_WEBDAV_PASSWORD), "s3cr3t-value")

    def test_overwrite(self) -> None:
        self.store.set("k", "a")
        self.store.set("k", "b")
        self.assertEqual(self.store.get("k"), "b")

    def test_delete_and_clear(self) -> None:
        self.store.set("a", "1")
        self.store.set("b", "2")
        self.assertTrue(self.store.delete("a"))
        self.assertFalse(self.store.delete("a"))
        self.assertEqual(self.store.keys(), ["b"])
        self.store.clear()
        self.assertEqual(self.store.keys(), [])
        self.assertFalse(self.path.exists())

    def test_require_raises_with_readable_hint(self) -> None:
        with self.assertRaises(CredentialError) as ctx:
            self.store.require(KEY_NETDISK_WEBDAV_PASSWORD)
        self.assertIn("OpenList 密码", str(ctx.exception))

    # -------------------------------------------------- 安全性质

    def test_file_has_magic_header(self) -> None:
        self.store.set("k", "v")
        self.assertTrue(self.path.read_bytes().startswith(b"QGB1"))

    def test_plaintext_never_on_disk(self) -> None:
        """最关键的一条：磁盘上不得出现凭据明文。"""
        secret = "SuperSecretRefreshToken-9f3a2b7c"
        self.store.set(KEY_NETDISK_WEBDAV_PASSWORD, secret)
        raw = self.path.read_bytes()
        self.assertNotIn(secret.encode(), raw)

    def test_tampered_file_rejected(self) -> None:
        self.store.set("k", "v")
        raw = bytearray(self.path.read_bytes())
        raw[-1] ^= 0xFF
        self.path.write_bytes(bytes(raw))
        broken = SecretStore(self.path, backend=InsecureDevBackend())
        with self.assertRaises(CredentialError):
            broken.keys()

    def test_bad_header_rejected(self) -> None:
        self.path.write_bytes(b"NOTQGB" + b"junk")
        broken = SecretStore(self.path, backend=InsecureDevBackend())
        with self.assertRaises(CredentialError):
            broken.keys()

    def test_oversized_payload_rejected(self) -> None:
        self.path.write_bytes(b"QGB1" + b"0" * (300 * 1024))
        broken = SecretStore(self.path, backend=InsecureDevBackend())
        with self.assertRaises(CredentialError):
            broken.keys()

    def test_values_registered_for_redaction(self) -> None:
        self.store.set("k", "RegisteredTokenValue123")
        out = redact_text("日志里出现了 RegisteredTokenValue123")
        self.assertNotIn("RegisteredTokenValue123", out)

    # -------------------------------------------------- 清单 / 自检

    def test_describe_masks_and_reports_no_profile_data(self) -> None:
        self.store.set(KEY_NETDISK_WEBDAV_USERNAME, "admin@example.com")
        self.store.set(KEY_NETDISK_WEBDAV_PASSWORD, "abcdefghijkl")
        rows = {r["key"]: r for r in self.store.describe()}

        self.assertIn(KEY_NETDISK_WEBDAV_PASSWORD, rows)
        password_row = rows[KEY_NETDISK_WEBDAV_PASSWORD]
        self.assertNotIn("abcdefghijkl", password_row["masked"])
        self.assertTrue(password_row["masked"].startswith("abcd"))

        # 不允许出现任何画像字段
        for row in self.store.describe():
            for forbidden in ("nickname", "vip", "membership", "quota", "phone", "email"):
                self.assertNotIn(forbidden, json.dumps(row, ensure_ascii=False))

    def test_health(self) -> None:
        self.store.set("a", "1")
        health = self.store.health()
        self.assertTrue(health["exists"])
        self.assertEqual(health["count"], 1)
        # 报告的是**实际注入的后端**（测试里是假后端），不能写死 DPAPI
        self.assertEqual(health["backend"], self.store.backend_name)

    def test_dpapi_backend_name_mentions_dpapi(self) -> None:
        from qgb.secrets import DpapiBackend

        self.assertIn("DPAPI", DpapiBackend.name)

    def test_set_many_is_atomic_enough(self) -> None:
        self.store.set_many({"a": "1", "b": "2", "c": "3"})
        self.assertEqual(self.store.keys(), ["a", "b", "c"])


if __name__ == "__main__":
    unittest.main()
