"""数据目录解析测试。

这一层是「受限账户上不能崩」的保障，必须有测试。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from qgb import paths


class TestDataDirResolution(unittest.TestCase):
    def setUp(self) -> None:
        paths.reset_cache()

    def tearDown(self) -> None:
        paths.reset_cache()

    def test_app_base_dir_exists(self) -> None:
        base = paths.app_base_dir()
        self.assertTrue(base.is_dir())
        self.assertTrue((base / "qgb").is_dir())

    def test_candidates_are_ordered(self) -> None:
        """候选目录的**顺序契约**：环境变量在首位、兜底在末位。

        ⚠️ 不要断言一定出现 ``%LOCALAPPDATA%``：Linux/容器里既没有
        ``LOCALAPPDATA`` 也没有 ``APPDATA``，代码会退化到「用户主目录」。
        本机（Windows）永远有这两个变量，所以以前只在 CI 上失败。
        """
        cands = paths.candidate_dirs()
        labels = [label for label, _ in cands]
        self.assertTrue(cands)
        # 最后一项必须是兜底
        self.assertIn("兜底", labels[-1])
        # 标准位置：Windows 用 %LOCALAPPDATA%，否则退化到用户主目录 —— 二者必居其一
        if os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA"):
            self.assertIn("%LOCALAPPDATA%", labels)
        else:
            self.assertIn("用户主目录", labels)

    def test_env_override_wins(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qgb-paths-") as tmp:
            custom = Path(tmp) / "custom-data"
            with mock.patch.dict(os.environ, {"QGB_DATA_DIR": str(custom)}):
                paths.reset_cache()
                resolved = paths.resolve_data_dir()
                self.assertEqual(resolved, custom.resolve())
                self.assertIn("QGB_DATA_DIR", paths.data_dir_source())
                self.assertTrue(resolved.is_dir())

    def test_falls_back_when_primary_unwritable(self) -> None:
        """模拟「%LOCALAPPDATA% 不可写」——必须降级而不是崩。"""
        with tempfile.TemporaryDirectory(prefix="qgb-paths-") as tmp:
            tmp_path = Path(tmp)
            blocked = tmp_path / "blocked"
            good = tmp_path / "good"

            real_probe = paths._probe_writable

            def fake_probe(path: Path) -> bool:
                if str(path).startswith(str(blocked)):
                    return False
                if str(path).startswith(str(good)):
                    path.mkdir(parents=True, exist_ok=True)
                    return True
                return real_probe(path)

            with mock.patch.dict(
                os.environ,
                {"QGB_DATA_DIR": str(blocked), "LOCALAPPDATA": str(good)},
                clear=False,
            ):
                paths.reset_cache()
                with mock.patch.object(paths, "_probe_writable", fake_probe):
                    resolved = paths.resolve_data_dir()
                self.assertTrue(paths.data_dir_source())  # 有来源说明
                self.assertFalse(str(resolved).startswith(str(blocked)))

    def test_portable_marker_detected(self) -> None:
        marker = paths.app_base_dir() / "portable.marker"
        created = False
        try:
            if not marker.exists():
                marker.write_text("portable", encoding="utf-8")
                created = True
            paths.reset_cache()
            self.assertTrue(paths.is_portable())
            self.assertIn("便携", " ".join(l for l, _ in paths.candidate_dirs()))
        finally:
            if created:
                marker.unlink(missing_ok=True)
            paths.reset_cache()

    def test_resolution_is_cached(self) -> None:
        first = paths.resolve_data_dir()
        second = paths.resolve_data_dir()
        self.assertEqual(first, second)

    def test_resolved_dir_is_writable(self) -> None:
        resolved = paths.resolve_data_dir()
        probe = resolved / ".test-writable"
        probe.write_text("ok", encoding="utf-8")
        self.assertEqual(probe.read_text(encoding="utf-8"), "ok")
        probe.unlink()

    def test_ensure_subdir(self) -> None:
        sub = paths.ensure_subdir("unit-test-subdir")
        self.assertTrue(sub.is_dir())
        try:
            sub.rmdir()
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
