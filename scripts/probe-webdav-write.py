"""最小 WebDAV 写测试：判定 405 的性质。

对照三组：
  1. 直接 PUT 一个小文件到已存在的目录
  2. 先 MKCOL 建目录再 PUT（看建目录本身是否也被拒）
  3. 用 PROPFIND 看目录属性

只写一个几字节的探针文件，结束后尝试删除。
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
    default_store_path,
)


def main() -> int:
    use_utf8_console()
    cfg = load_config(Path("dist/QQ群文件搬运工/data/config.json"))
    root = cfg.upload.remote_root.rstrip("/")
    base = cfg.upload.webdav_url.rstrip("/")

    store = SecretStore(default_store_path())
    user = store.get(KEY_NETDISK_WEBDAV_USERNAME) or ""
    pwd = store.get(KEY_NETDISK_WEBDAV_PASSWORD) or ""
    auth = (user, pwd) if user else None
    print("=" * 66)
    print("最小 WebDAV 写测试")
    print("=" * 66)
    print(f"  URL : {base}")
    print(f"  根  : {root}")
    print(f"  认证: {'已配置用户名 ' + user if user else '（无）'}")

    s = requests.Session()
    if auth:
        s.auth = auth

    # 0) PROPFIND 根目录
    r = s.request("PROPFIND", f"{base}{root}/", headers={"Depth": "0"}, timeout=(8, 30))
    print(f"\n  [0] PROPFIND {root}/  → HTTP {r.status_code}")

    # 1) 直接 PUT 到已存在的根目录
    probe = f"{root}/_qgb_write_probe.txt"
    r = s.put(f"{base}{probe}", data=b"qgb-probe", timeout=(8, 60))
    print(f"  [1] PUT {probe}  → HTTP {r.status_code}  ({len(b'qgb-probe')} 字节)")
    if r.status_code >= 400:
        print(f"      响应体: {r.text[:200]!r}")

    # 2) MKCOL 一个子目录再 PUT
    sub = f"{root}/_qgb_probe_dir"
    r = s.request("MKCOL", f"{base}{sub}/", timeout=(8, 60))
    print(f"  [2] MKCOL {sub}/  → HTTP {r.status_code}")
    r2 = s.put(f"{base}{sub}/inner.txt", data=b"inner", timeout=(8, 60))
    print(f"      PUT {sub}/inner.txt  → HTTP {r2.status_code}")

    # 3) 对照：写一个较大的文件（1MB），看是否与体积有关
    big = f"{root}/_qgb_write_probe_big.bin"
    r3 = s.put(f"{base}{big}", data=b"x" * (1024 * 1024), timeout=(15, 120))
    print(f"  [3] PUT 1MB 探针 → HTTP {r3.status_code}")

    # 清理
    for path in (probe, f"{sub}/inner.txt", big):
        s.delete(f"{base}{path}", timeout=(8, 30))
    s.request("DELETE", f"{base}{sub}/", timeout=(8, 30))

    print("\n" + "=" * 66)
    print("判读：")
    print("  · 若小文件也 405 → WebDAV 写入被整体拒绝（账号权限或驱动不支持）")
    print("  · 若仅大文件 405 → 与体积/超时有关")
    print("  · 若都成功 → 之前那次是偶发（或与具体路径有关）")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
