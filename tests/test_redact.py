"""脱敏层测试 —— 这是「凭据不外泄」的主要防线。"""

from __future__ import annotations

import unittest

from qgb.redact import (
    clear_registered,
    mask_id,
    mask_secret,
    redact_text,
    register_secret,
)


class TestMasking(unittest.TestCase):
    def tearDown(self) -> None:
        clear_registered()

    def test_mask_secret_keeps_head_tail(self) -> None:
        out = mask_secret("abcdefghijklmnop")
        self.assertTrue(out.startswith("abcd"))
        self.assertTrue(out.endswith("mnop"))
        self.assertIn("…", out)
        self.assertNotIn("efghijkl", out)

    def test_mask_secret_short_value_fully_hidden(self) -> None:
        self.assertEqual(mask_secret("abc"), "***")
        self.assertEqual(mask_secret(""), "(空)")
        self.assertEqual(mask_secret(None), "(空)")

    def test_mask_id(self) -> None:
        self.assertEqual(mask_id("123456789"), "123***789")
        self.assertEqual(mask_id("12345"), "*****")


class TestRedactText(unittest.TestCase):
    def tearDown(self) -> None:
        clear_registered()

    def test_json_token(self) -> None:
        text = '{"refresh_token": "abcdef123456", "user": "x"}'
        out = redact_text(text)
        self.assertNotIn("abcdef123456", out)
        self.assertIn("***", out)
        self.assertIn('"user"', out)

    def test_query_string_token(self) -> None:
        out = redact_text("https://pan.example.com/api?access_token=SECRET_VALUE&x=1")
        self.assertNotIn("SECRET_VALUE", out)
        self.assertIn("x=1", out)

    def test_signature_param(self) -> None:
        out = redact_text("https://d.pcs.baidu.com/file/x?sign=AAAA1111BBBB&time=1")
        self.assertNotIn("AAAA1111BBBB", out)
        self.assertIn("time=1", out)

    def test_bearer_header(self) -> None:
        out = redact_text("Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6")
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6", out)

    def test_bduss_and_stoken(self) -> None:
        out = redact_text("BDUSS=abcdefghijklmnopqrst; STOKEN=0123456789abcdef")
        self.assertNotIn("abcdefghijklmnopqrst", out)
        self.assertNotIn("0123456789abcdef", out)

    def test_phone_number(self) -> None:
        out = redact_text("联系 13812345678 处理")
        self.assertNotIn("13812345678", out)
        self.assertIn("138", out)

    def test_password_field(self) -> None:
        out = redact_text('password="hunter2xyz"')
        self.assertNotIn("hunter2xyz", out)

    def test_registered_secret_anywhere(self) -> None:
        register_secret("MyLongSecretToken123")
        out = redact_text("上传时使用 MyLongSecretToken123 作为凭据")
        self.assertNotIn("MyLongSecretToken123", out)

    def test_short_registered_value_is_ignored(self) -> None:
        register_secret("abc")  # 太短，不应参与替换，避免误伤
        self.assertEqual(redact_text("abc 是普通词"), "abc 是普通词")

    def test_unicode_preserved(self) -> None:
        out = redact_text("中文与 emoji 🐦 保持原样")
        self.assertIn("中文与 emoji 🐦 保持原样", out)

    def test_empty_input(self) -> None:
        self.assertEqual(redact_text(""), "")


if __name__ == "__main__":
    unittest.main()
