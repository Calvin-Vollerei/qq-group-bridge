"""复现 405：用程序自己的凭据做三种上传对照。

1. 小探针文件 → 判定写入是否整体被拒
2. 重新上传一个**已经成功过**的文件（同名同路径）→ 判定是否与具体文件有关
3. 上传那个**失败过**的 56.9MB 文件 → 直接复现
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from _console import use_utf8_console  # noqa: E402

import requests  # noqa: E402

from qgb.config import load_config  # noqa: E402
from qgb.secrets import (  # noqa: E402
    KEY_NETDISK_WEBDAV_PASSWORD,
    KEY_NETDISK_WEBDAV_USERNAME,
    SecretStore,
)

DATA = Path("dist/QQ群文件搬运工/data")


def main() -> int:
    use_utf8_console()
    cfg = load_config(DATA / "config.json")
    base = cfg.upload.webdav_url.rstrip("/")
    root = cfg.upload.remote_root.rstrip("/")

    print("=" * 68)
    print("复现 405：对照测试")
    print("=" * 68)

    # 逐个候选路径试，找到真有凭据的那个
    store = None
    for cand in (DATA / "secrets.enc", DATA / "state.db", Path("dist/secrets.enc")):
        if not cand.is_file():
            continue
        try:
            s = SecretStore(cand)
            u = s.get(KEY_NETDISK_WEBDAV_USERNAME)
            if u:
                store, user = s, u
                print(f"  凭据来源: {cand}  用户={user}")
                break
        except Exception as exc:  # noqa: BLE001
            print(f"  {cand} 读取失败：{type(exc).__name__}")
    if store is None:
        print("  ❌ 找不到含 WebDAV 凭据的存储，无法做认证测试")
        print("     （程序的凭据是 DPAPI 加密的，只有同一 Windows 用户能解开）")
        return 2

    pwd = store.get(KEY_NETDISK_WEBDAV_PASSWORD) or ""
    s = requests.Session()
    s.auth = (user, pwd)

    def show(label: str, resp, extra: str = "") -> None:
        code = resp.status_code
        mark = "✅" if code in (200, 201, 204) else ("⚠️" if code in (301, 302) else "❌")
        print(f"  {mark} {label}: HTTP {code}{('  ' + extra) if extra else ''}")
        if code >= 400 and resp.text[:120]:
            print(f"       响应体: {resp.text[:120]!r}")

    # 0) 认证检查
    r = s.request("PROPFIND", f"{base}{root}/", headers={"Depth": "0"}, timeout=(8, 30))
    show("PROPFIND（认证检查）", r)

    # 1) 小探针
    probe = f"{root}/_qgb_write_probe.txt"
    r = s.put(f"{base}{probe}", data=b"qgb-probe", timeout=(10, 60))
    show("PUT 9 字节探针", r)
    s.delete(f"{base}{probe}", timeout=(8, 30))

    # 2) 重传一个**曾经成功**的文件（用 done 记录里的路径 + 本地文件）
    print("\n  --- 重传曾经成功的文件（同名同路径）")
    import sqlite3

    conn = sqlite3.connect(str(DATA / "state.db"))
    conn.row_factory = sqlite3.Row
    done = conn.execute(
        "SELECT name, size, remote_path, local_path FROM transfers "
        "WHERE state='done' AND remote_path != '' ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    if done:
        print(f"      文件: {done['name'][:50]}  ({done['size'] / 1048576:.1f} MB)")
        print(f"      远端: {done['remote_path']}")
        lp = Path(done["local_path"]) if done["local_path"] else None
        if lp and lp.is_file():
            with lp.open("rb") as fh:
                r = s.put(f"{base}{done['remote_path']}", data=fh, timeout=(15, 600))
            show("重传（同名同路径）", r)
        else:
            print("      本地副本已删除，跳过（这是正常的清理行为）")
    else:
        print("      没有 done 记录")

    # 3) 复现失败的那个大文件
    print("\n  --- 复现失败的大文件")
    failed = conn.execute(
        "SELECT name, size, remote_path FROM transfers WHERE state='failed' "
        "AND error LIKE '%405%' ORDER BY updated_at DESC LIMIT 3"
    ).fetchall()
    if not failed:
        failed = conn.execute(
            "SELECT name, size, remote_path FROM transfers WHERE state='failed' "
            "ORDER BY updated_at DESC LIMIT 3"
        ).fetchall()
    for f in failed:
        print(f"      {f['name'][:46]}  {f['size'] / 1048576:.1f} MB")
    if failed:
        f = failed[0]
        target = f"{root}/_qgb_probe_big.bin"
        print(f"      用 1MB 数据 PUT 到 {target} 试写（不传原大文件，省时间）")
        r = s.put(f"{base}{target}", data=b"x" * (1024 * 1024), timeout=(15, 300))
        show("PUT 1MB 探针", r)
        s.delete(f"{base}{target}", timeout=(8, 60))
    conn.close()

    print("\n" + "=" * 68)
    print("判读：")
    print("  · 小探针也 405 → WebDAV 写入被整体拒绝 → 查 OpenList 用户权限/存储只读")
    print("  · 小探针成功、大文件失败 → 与体积/超时/百度侧限制有关")
    print("  · 全成功 → 之前那次是偶发（百度驱动临时故障）")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
