"""网盘目录命名测试。

守的是「文件到底放进哪个子目录」。这里有两条不能破的底线：

  * **目录名必须安全**：群名是用户随便起的，可能含 ``/`` ``..`` 或
    Windows 保留名（``CON``），直接拿来当目录名会造出意外层级或被拒绝。
  * **任何情况下都要有可用的目录名**：群名取不到、清洗后为空，都必须
    退回群号 —— 名字难看可以接受，搬运失败不行。
"""

from __future__ import annotations

import unittest

from qgb.config import UploadConfig
from qgb.naming import (
    FOLDER_STYLES,
    format_folder_map,
    has_folder_conflicts,
    parse_folder_map,
    resolve_group_folder,
    sanitize_folder,
)

GID = "123456789"
GNAME = "示例学习群"


class SanitizeTest(unittest.TestCase):
    def test_illegal_path_chars_replaced(self) -> None:
        """``/ \\ : * ? " < > |`` 都不能出现在单层目录名里。"""
        cleaned = sanitize_folder('A/B\\C:D*E?F"G<H>I|J')
        for bad in '/\\:*?"<>|':
            self.assertNotIn(bad, cleaned, f"{bad!r} 不该留下")
        self.assertIn("A", cleaned)

    def test_control_chars_stripped(self) -> None:
        self.assertNotIn("\x00", sanitize_folder("a\x00b\x1fc"))

    def test_dots_only_becomes_fallback(self) -> None:
        """``.`` / ``..`` 会造成路径穿越或隐藏目录，必须清掉。"""
        self.assertEqual(sanitize_folder("..", fallback=GID), GID)
        self.assertEqual(sanitize_folder(".", fallback=GID), GID)
        self.assertEqual(sanitize_folder("...", fallback=GID), GID)

    def test_leading_trailing_dots_and_spaces_removed(self) -> None:
        self.assertEqual(sanitize_folder("  .群名.  "), "群名")

    def test_windows_reserved_names_prefixed(self) -> None:
        """``CON`` / ``NUL`` / ``COM1`` 作为目录名会被 Windows 拒绝。"""
        for name in ("CON", "con", "NUL", "COM1", "LPT9"):
            cleaned = sanitize_folder(name, fallback=GID)
            self.assertNotEqual(cleaned.lower(), name.lower())
            self.assertTrue(cleaned)

    def test_full_width_separators_normalized(self) -> None:
        """全角斜杠看起来像分隔符，容易被漏掉。"""
        cleaned = sanitize_folder("A／B＼C")
        self.assertNotIn("／", cleaned)
        self.assertNotIn("＼", cleaned)

    def test_length_capped(self) -> None:
        long_name = "长" * 300
        self.assertLessEqual(len(sanitize_folder(long_name, fallback=GID)), 80)

    def test_empty_falls_back(self) -> None:
        self.assertEqual(sanitize_folder("", fallback=GID), GID)
        self.assertEqual(sanitize_folder("   ", fallback=GID), GID)

    def test_chinese_and_spaces_kept(self) -> None:
        self.assertEqual(sanitize_folder("示例学习群 2.0"), "示例学习群 2.0")


class ResolveFolderTest(unittest.TestCase):
    def test_style_id(self) -> None:
        self.assertEqual(
            resolve_group_folder(GID, style="id", group_name=GNAME), GID
        )

    def test_style_name(self) -> None:
        self.assertEqual(
            resolve_group_folder(GID, style="name", group_name=GNAME), GNAME
        )

    def test_style_id_name(self) -> None:
        self.assertEqual(
            resolve_group_folder(GID, style="id_name", group_name=GNAME),
            f"{GID}_{GNAME}",
        )

    def test_custom_beats_style(self) -> None:
        """用户显式指定的名字优先级最高。"""
        self.assertEqual(
            resolve_group_folder(
                GID, custom="我自己起的", style="id_name", group_name=GNAME
            ),
            "我自己起的",
        )

    def test_custom_is_sanitized(self) -> None:
        self.assertNotIn("/", resolve_group_folder(GID, custom="A/B"))

    def test_missing_group_name_falls_back_to_id(self) -> None:
        """**关键**：群名取不到时不能产出空目录名。

        否则路径会塌成 ``<根>/<文件名>``，几百个群的文件全糊在一起。
        """
        for style in ("name", "id_name"):
            self.assertEqual(
                resolve_group_folder(GID, style=style, group_name=""), GID, style
            )

    def test_group_name_with_only_illegal_chars(self) -> None:
        self.assertEqual(
            resolve_group_folder(GID, style="name", group_name="///"), GID
        )

    def test_unknown_style_falls_back_to_id(self) -> None:
        self.assertEqual(
            resolve_group_folder(GID, style="乱写的", group_name=GNAME), GID
        )

    def test_empty_group_id(self) -> None:
        self.assertEqual(resolve_group_folder("", style="id"), "")

    def test_all_advertised_styles_work(self) -> None:
        """界面上列出的每种风格都必须真的有效。"""
        for style in FOLDER_STYLES:
            folder = resolve_group_folder(GID, style=style, group_name=GNAME)
            self.assertTrue(folder, style)
            self.assertNotIn("/", folder, style)


class ParseFolderMapTest(unittest.TestCase):
    def test_equals_separator(self) -> None:
        self.assertEqual(parse_folder_map("123 = 名字"), {"123": "名字"})

    def test_colon_separators(self) -> None:
        self.assertEqual(parse_folder_map("123: 名字"), {"123": "名字"})
        self.assertEqual(parse_folder_map("123：名字"), {"123": "名字"})

    def test_whitespace_separator(self) -> None:
        self.assertEqual(parse_folder_map("123 名字"), {"123": "名字"})

    def test_comments_and_blank_lines_ignored(self) -> None:
        text = "# 这是注释\n\n123 = 名字\n  \n"
        self.assertEqual(parse_folder_map(text), {"123": "名字"})

    def test_non_numeric_group_ignored(self) -> None:
        """说明文字不该被当成配置。"""
        self.assertEqual(parse_folder_map("随便写点什么"), {})
        self.assertEqual(parse_folder_map("abc = 名字"), {})

    def test_roundtrip(self) -> None:
        mapping = {"2": "乙", "1": "甲"}
        self.assertEqual(parse_folder_map(format_folder_map(mapping)), mapping)

    def test_format_skips_empty_values(self) -> None:
        self.assertEqual(format_folder_map({"1": "甲", "2": ""}), "1 = 甲")


class ConflictTest(unittest.TestCase):
    def test_detects_two_groups_same_folder(self) -> None:
        conflicts = has_folder_conflicts({"1": "同一个", "2": "同一个"}, ["1", "2"])
        self.assertEqual(len(conflicts), 1)
        self.assertIn("1", conflicts[0])
        self.assertIn("2", conflicts[0])

    def test_no_conflict_when_distinct(self) -> None:
        self.assertEqual(has_folder_conflicts({"1": "甲", "2": "乙"}, ["1", "2"]), [])

    def test_groups_without_custom_use_id_so_never_conflict(self) -> None:
        self.assertEqual(has_folder_conflicts({}, ["1", "2", "3"]), [])


class RemotePathIntegrationTest(unittest.TestCase):
    """目录名要真的落到 WebDAV / 本地路径上。"""

    def _path(self, **kw) -> str:
        from qgb.uploaders.webdav import WebDAVUploader

        class _S:
            def get(self, k):
                return ""

            def set(self, k, v):
                pass

        cfg = UploadConfig(
            adapter="webdav",
            remote_root=kw.pop("remote_root", "/baidu/QQ群备份"),
            split_by_group=kw.pop("split_by_group", True),
        )
        return WebDAVUploader(cfg, _S()).build_remote_path(**kw)

    def test_folder_overrides_group_id(self) -> None:
        self.assertEqual(
            self._path(group_id=GID, filename="x.pdf", folder=GNAME),
            f"/baidu/QQ群备份/{GNAME}/x.pdf",
        )

    def test_empty_folder_falls_back_to_group_id(self) -> None:
        """旧调用方（不传 folder）行为不变。"""
        self.assertEqual(
            self._path(group_id=GID, filename="x.pdf"),
            f"/baidu/QQ群备份/{GID}/x.pdf",
        )

    def test_folder_ignored_when_split_disabled(self) -> None:
        self.assertEqual(
            self._path(group_id=GID, filename="x.pdf", folder=GNAME,
                       split_by_group=False),
            "/baidu/QQ群备份/x.pdf",
        )

    def test_no_double_slash(self) -> None:
        path = self._path(group_id=GID, filename="x.pdf", folder=GNAME)
        self.assertNotIn("//", path)


if __name__ == "__main__":
    unittest.main()
