"""真机探针：为什么 get_group_file_url 报 real fileUUID not found。

对「失败集中的群」和「正常群」各取一个文件，直接调 NapCat 取直链，
对比两者的返回；并尝试用不同参数（busid / 只用纯数字 file_id）复现/绕过。

只读操作，不下载、不修改任何状态。
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from _console import use_utf8_console  # noqa: E402

import requests  # noqa: E402

DB = Path("dist/QQ群文件搬运工/data/state.db")
CFG = Path("dist/QQ群文件搬运工/data/config.json")


def load_api_base() -> str:
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    return str(cfg["napcat"]["api_base"]).rstrip("/")


def call(api: str, action: str, params: dict) -> tuple[bool, str]:
    try:
        r = requests.post(f"{api}/{action}", json=params, timeout=(6, 30))
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败：{type(exc).__name__}: {exc}"
    try:
        body = r.json()
    except ValueError:
        return False, f"非 JSON 响应（HTTP {r.status_code}）"
    ok = str(body.get("status")) in ("ok", "async") and body.get("retcode") in (0, None)
    msg = body.get("message") or body.get("msg") or str(body)[:200]
    data = body.get("data")
    if ok and isinstance(data, dict):
        url = data.get("url") or ""
        if url:
            return True, f"✅ 取到直链：{str(url)[:80]}…"
        return True, f"返回成功但没有 url 字段：{str(data)[:120]}"
    return False, f"❌ {msg}"


def main() -> int:
    use_utf8_console()
    api = load_api_base()
    print("=" * 66)
    print("探针：get_group_file_url 为何报 real fileUUID not found")
    print("=" * 66)
    print(f"  NapCat API: {api}")

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    def pick(state: str, group: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM transfers WHERE state=? AND group_id=? LIMIT 1", (state, group)
        ).fetchone()

    cases = [
        ("失败集中的群 + 失败的条目", pick("failed", "922161761")),
        ("正常群 + 成功的条目", pick("done", "1030101804")),
        ("失败集中的群 + 成功的条目", pick("done", "922161761")),
        ("正常群 + 失败的条目", pick("failed", "1030101804")),
    ]

    # 先确认登录状态
    try:
        r = requests.post(f"{api}/get_login_info", json={}, timeout=(6, 20)).json()
        info = r.get("data") or {}
        print(f"  登录状态: {info.get('nickname') or info.get('user_id') or r.get('message')}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 组件连不上：{exc}")
        print("     请先在程序里点「启动组件」")
        return 2

    print()
    for label, row in cases:
        if row is None:
            print(f"  [{label}] 没有样本，跳过")
            continue
        print(f"  [{label}]")
        print(f"     群={row['group_id']}  file_id={row['file_id']}  busid={row['busid']}")
        print(f"     文件={row['name'][:44]}")
        # 1) 原样调用
        ok, msg = call(api, "get_group_file_url",
                       {"group_id": row["group_id"], "file_id": row["file_id"],
                        "busid": int(row["busid"])})
        print(f"     原样调用      : {msg}")
        # 2) 不带 busid（默认值）
        ok2, msg2 = call(api, "get_group_file_url",
                         {"group_id": row["group_id"], "file_id": row["file_id"]})
        print(f"     不带 busid    : {msg2}")
        # 3) 备用动作名
        ok3, msg3 = call(api, "get_group_file_download_url",
                         {"group_id": row["group_id"], "file_id": row["file_id"],
                          "busid": int(row["busid"])})
        print(f"     备用动作名    : {msg3}")
        print()
        time.sleep(1.2)          # 别把组件打急

    conn.close()
    print("=" * 66)
    print("说明：若「失败集中的群」里**新取的**条目也失败、而成功群里怎么试都成功，")
    print("      则是该群在 NapCat 侧的文件 UUID 缓存问题（与我们的调用参数无关）。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
