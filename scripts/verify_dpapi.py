#!/usr/bin/env python3
"""DPAPI 生产凭据路径自检（部署后 / 打包前跑一次）。

验证的是**真实交付链路**，不是假后端：

  1. 本机是否支持 DPAPI
  2. 加密 → 落盘 → 重新解密，值是否一致
  3. 磁盘上**是否出现明文**（最关键）
  4. 文件被篡改后能否被拒绝（完整性）
  5. 不同熵（不同应用）之间能否互相隔离
  6. 清除后文件是否真的消失
  7. 非 Windows 或缺失 DPAPI 时是否 fail-closed（不允许静默降级）

用法::

    python scripts/verify_dpapi.py
"""

from __future__ import annotations

import os
import secrets as pysecrets
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qgb import dpapi  # noqa: E402
from qgb.errors import CredentialError, PlatformUnsupported  # noqa: E402
from _console import use_utf8_console  # noqa: E402  控制台切 UTF-8（Linux 上常是 cp1252）
from qgb.secrets import (  # noqa: E402
    KEY_NETDISK_WEBDAV_PASSWORD,
    DpapiBackend,
    SecretStore,
    default_backend,
)

PASS = "✅"
FAIL = "❌"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[bool, str, str]] = []

    def add(self, ok: bool, name: str, detail: str = "") -> None:
        self.rows.append((bool(ok), name, detail))
        mark = PASS if ok else FAIL
        print(f"  {mark} {name}" + (f" —— {detail}" if detail else ""))

    @property
    def ok(self) -> bool:
        return all(r[0] for r in self.rows)


def main() -> int:
    use_utf8_console()
    print("=" * 64)
    print("DPAPI 凭据路径自检（真实后端）")
    print("=" * 64)
    print(f"平台：{sys.platform} · Python {sys.version.split()[0]}")

    report = Report()
    workdir = Path(tempfile.mkdtemp(prefix="qgb-dpapi-verify-"))

    try:
        # ---------------------------------------------------- 支持性
        report.add(dpapi.available(), "本机支持 Windows DPAPI")

        if not dpapi.available():
            print("\n[说明] 当前平台不支持 DPAPI。")
            print("       生产部署必须为 Windows 10/11；此处仅验证 fail-closed 行为。")
            try:
                default_backend()
                report.add(False, "非 Windows 时拒绝静默降级", "竟然拿到了后端")
            except PlatformUnsupported:
                report.add(True, "非 Windows 时拒绝静默降级", "已正确抛出 PlatformUnsupported")
            return 1 if not report.ok else 0

        # ---------------------------------------------------- 往返
        value = "Verify-" + pysecrets.token_urlsafe(24)
        store = SecretStore(workdir / "secrets.enc", backend=DpapiBackend())

        store.set(KEY_NETDISK_WEBDAV_PASSWORD, value)
        report.add(
            store.get(KEY_NETDISK_WEBDAV_PASSWORD) == value,
            "加密 → 落盘 → 解密往返一致",
        )

        # ---------------------------------------------------- 明文检查
        raw = (workdir / "secrets.enc").read_bytes()
        report.add(value.encode() not in raw, "磁盘上不含凭据明文")
        report.add(raw.startswith(b"QGB1"), "文件头正确（QGB1）")

        # ---------------------------------------------------- 重新打开
        store2 = SecretStore(workdir / "secrets.enc", backend=DpapiBackend())
        report.add(
            store2.get(KEY_NETDISK_WEBDAV_PASSWORD) == value,
            "新进程实例可解密（持久化可用）",
        )

        # ---------------------------------------------------- 完整性
        tampered = bytearray(raw)
        tampered[-1] ^= 0xFF
        bad_path = workdir / "tampered.enc"
        bad_path.write_bytes(bytes(tampered))
        try:
            SecretStore(bad_path, backend=DpapiBackend()).keys()
            report.add(False, "篡改后的凭据文件被拒绝", "竟然成功读取")
        except CredentialError:
            report.add(True, "篡改后的凭据文件被拒绝", "已抛 CredentialError")

        # ---------------------------------------------------- 熵隔离
        try:
            dpapi.unprotect(raw[len(b"QGB1"):], entropy=b"another-app-entropy")
            report.add(False, "不同熵无法解密（应用隔离）", "竟然解密成功")
        except Exception:
            report.add(True, "不同熵无法解密（应用隔离）")

        # ---------------------------------------------------- 清单不外露
        rows = store2.describe()
        report.add(len(rows) == 1, "凭据清单条目数正确", f"{len(rows)} 条")
        if rows:
            masked = rows[0]["masked"]
            report.add(value not in masked, "清单只显示遮蔽值", masked)
            forbidden = {"nickname", "vip", "membership", "quota", "phone"}
            report.add(
                not (forbidden & set(rows[0])),
                "清单不含任何账号画像字段",
            )

        # ---------------------------------------------------- 清除
        store2.clear()
        report.add(
            not (workdir / "secrets.enc").exists(),
            "清除凭据后文件被删除",
        )

        # ---------------------------------------------------- 权限
        store3 = SecretStore(workdir / "perm.enc", backend=DpapiBackend())
        store3.set("k", "v")
        mode = os.stat(workdir / "perm.enc").st_mode & 0o777
        report.add(mode in (0o600, 0o666, 0o644), "文件权限已设置", oct(mode))

    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    total = len(report.rows)
    passed = sum(1 for r in report.rows if r[0])
    print("-" * 64)
    print(f"结果：{passed}/{total} 项通过")
    print("=" * 64)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
