"""验证「先刷新列表，再取直链」是否可行 —— 针对 real fileUUID not found。

假设：NapCat 取直链需要内部的 fileUUID 映射，而映射会随组件重启/会话变化失效。
若成立，则「先重新拉一次群文件列表（刷新映射），紧接着立刻取直链」应当成功。

步骤（全部只读，不下载文件）：
  1. 调用 get_group_root_files 拿一份**新鲜**的文件列表
  2. 从返回里取前几个文件，看返回字段里有没有 fileUUID 之类的字段
  3. 对同一个文件：先在「无刷新」情况下取直链，再刷新列表后立刻取直链，对比结果
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from _console import use_utf8_console  # noqa: E402

import requests  # noqa: E402

CFG = Path("dist/QQ群文件搬运工/data/config.json")
GROUP = "922161761"          # 失败最集中的群


def post(api: str, action: str, params: dict, timeout=(6, 40)) -> dict:
    try:
        return requests.post(f"{api}/{action}", json=params, timeout=timeout).json()
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "message": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    use_utf8_console()
    api = str(json.loads(CFG.read_text(encoding="utf-8"))["napcat"]["api_base"]).rstrip("/")
    print("=" * 66)
    print("验证：先刷新列表再取直链")
    print("=" * 66)

    # ---------- 1. 拉一份新鲜列表，看字段里有没有 uuid ----------
    print("\n[1] get_group_root_files（新鲜列表）")
    body = post(api, "get_group_root_files", {"group_id": GROUP, "file_count": 20})
    data = body.get("data") or {}
    files = data.get("files") or []
    print(f"    返回文件数: {len(files)}")
    if files:
        keys = sorted(files[0].keys())
        print(f"    返回字段: {keys}")
        print("    --- 前 3 条的关键字段:")
        for f in files[:3]:
            print(f"       file_id={str(f.get('file_id'))[:36]}")
            for k in ("file_name", "file_uuid", "uuid", "fileUUID", "busid"):
                if k in f:
                    print(f"          {k} = {str(f[k])[:60]}")

    # ---------- 2. 挑一个文件，先不刷新直接取直链 ----------
    if not files:
        print("    ❌ 列表为空，无法继续")
        return 2
    target = files[0]
    fid = str(target.get("file_id") or "")
    busid = int(target.get("busid") or 102)
    print(f"\n[2] 目标文件: {str(target.get('file_name'))[:50]}")
    print(f"    file_id={fid[:36]}  busid={busid}")

    r0 = post(api, "get_group_file_url", {"group_id": GROUP, "file_id": fid, "busid": busid})
    ok0 = str(r0.get("status")) == "ok"
    print(f"    不刷新列表 → 直接取直链: {'✅ 成功' if ok0 else '❌ ' + str(r0.get('message'))[:60]}")

    # ---------- 3. 刷新列表后立刻取直链 ----------
    print("\n[3] 再拉一次列表（刷新），紧接着立刻取直链")
    post(api, "get_group_root_files", {"group_id": GROUP, "file_count": 20})
    time.sleep(0.3)
    r1 = post(api, "get_group_file_url", {"group_id": GROUP, "file_id": fid, "busid": busid})
    ok1 = str(r1.get("status")) == "ok"
    print(f"    刷新后取直链: {'✅ 成功' if ok1 else '❌ ' + str(r1.get('message'))[:60]}")
    if ok1:
        url = (r1.get("data") or {}).get("url") or ""
        print(f"    直链: {str(url)[:90]}…")

    print("\n" + "=" * 66)
    if ok1 and not ok0:
        print("结论：**刷新列表后就成功了** —— 修复方向是「取直链前先刷新该群列表」。")
    elif ok0:
        print("结论：不刷新也能成功 —— 说明之前失败的是其它原因（可能是当时组件刚重启）。")
    else:
        print("结论：刷新列表也没用 —— NapCat 侧该文件的 UUID 确实拿不到，")
        print("      需要换获取途径（例如从群文件消息里取，或改用网页端分享链接）。")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
