"""判定「fileUUID 未就绪」到底是临时状态，还是文件已不在群里。

做法：把库里「待处理」的 file_id 与**当前群文件列表**里的 file_id 做集合比对。

* 若待处理项**大量不在**当前列表里 → 它们是**陈旧记录**（文件已被删除/重命名，
  或 file_id 随会话变了），NapCat 自然查不到 fileUUID —— 这类记录该被清掉，
  而不是反复重试。
* 若待处理项**都在**列表里却仍取不到直链 → 才是真正的会话状态问题。

只读操作。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from _console import use_utf8_console  # noqa: E402

import requests  # noqa: E402

DB = Path("dist/QQ群文件搬运工/data/state.db")
CFG = Path("dist/QQ群文件搬运工/data/config.json")


def post(api: str, action: str, params: dict, timeout=(6, 60)) -> dict:
    try:
        return requests.post(f"{api}/{action}", json=params, timeout=timeout).json()
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    use_utf8_console()
    api = str(json.loads(CFG.read_text(encoding="utf-8"))["napcat"]["api_base"]).rstrip("/")
    print("=" * 70)
    print("探针：待处理项是否还在群文件列表里")
    print("=" * 70)

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    groups = [
        r["group_id"]
        for r in conn.execute(
            "SELECT group_id, COUNT(*) n FROM transfers WHERE state='discovered' "
            "GROUP BY group_id ORDER BY n DESC LIMIT 4"
        )
    ]
    print(f"  待处理涉及 {len(groups)} 个群（取前 4 个）")

    for gid in groups:
        pending = {
            r["file_id"]: r["name"]
            for r in conn.execute(
                "SELECT file_id, name FROM transfers WHERE state='discovered' AND group_id=?",
                (gid,),
            )
        }
        names = {v for v in pending.values()}

        # 拉当前群文件列表（取大一点）
        body = post(api, "get_group_root_files", {"group_id": gid, "file_count": 1000})
        data = body.get("data") or {}
        live = data.get("files") or []
        live_ids = {str(f.get("file_id") or "") for f in live}
        live_names = {str(f.get("file_name") or "") for f in live}

        hit_id = len(set(pending) & live_ids)
        hit_name = len(names & live_names)
        print(f"\n  群 {gid}")
        print(f"    待处理 {len(pending)} 条 | 当前列表返回 {len(live)} 条")
        print(f"    按 file_id 命中：{hit_id}  ({hit_id * 100 // max(1, len(pending))}%)")
        print(f"    按文件名命中  ：{hit_name}  ({hit_name * 100 // max(1, len(names))}%)")
        if live:
            print(f"    列表样例：{[str(f.get('file_name'))[:24] for f in live[:3]]}")
        if pending:
            print(f"    待处理样例：{[v[:24] for v in list(pending.values())[:3]]}")

        # 对"当前列表里确实存在"的文件试一次取直链
        if live:
            t = live[0]
            r = post(api, "get_group_file_url",
                     {"group_id": gid, "file_id": str(t.get("file_id")),
                      "busid": int(t.get("busid") or 102)})
            ok = str(r.get("status")) == "ok"
            print(f"    对列表内文件取直链：{'✅ 成功' if ok else '❌ ' + str(r.get('message'))[:50]}")

    conn.close()
    print("\n" + "=" * 70)
    print("判读：")
    print("  · 待处理项大量不在当前列表 → 陈旧记录，应清理而不是重试")
    print("  · 待处理项都在列表里却取不到直链 → 才是 NapCat 会话状态问题")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
