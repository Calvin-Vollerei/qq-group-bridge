#!/usr/bin/env python3
"""诊断百度网盘 refresh_token 是否有效。

背景
----
OpenList 的百度驱动在勾选「Use online api」时，会调用 OpenList 官方的中转服务：

    GET https://api.oplist.org/baiduyun/renewapi
        ?refresh_ui=<你的 refresh_token>&server_use=true&driver_txt=baiduyun_go

        （见 OpenList 源码 drivers/baidu_netdisk/util.go 的 _refreshToken）

成功时返回 ``{"refresh_token": "...", "access_token": "..."}``；
失败时可能返回 ``{"text": "<错误原因>"}``；
两者都没有 —— 就是 OpenList 界面里那句：

    empty token returned from official API, a wrong refresh token may have been used

这个脚本把**原始回应**打出来，省得你在 OpenList 界面和浏览器之间来回猜。

安全说明
--------
* 令牌从 **标准输入** 读取，不作为命令行参数（避免进入 shell 历史/进程列表）
* 只打印**长度与前若干字符**用于核对，**不打印完整令牌**
* 不写任何文件

用法::

    python scripts/test_baidu_token.py
    # 然后粘贴 refresh_token 回车
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

#: 与 OpenList 驱动里完全一致的端点（别改错，改错就是另一种失败）
ENDPOINT = "https://api.oplist.org/baiduyun/renewapi"


def _read_token(args) -> str:
    """取令牌。

    ⚠️ 为什么要有 ``--file``：**PowerShell 往管道里写字符串时会带上 BOM**，
    于是令牌前面多出一个不可见的 U+FEFF，百度当然认不出来 —— 这会让人
    误判成"令牌失效"。从文件读（且文件按 UTF-8 无 BOM 写）才能排除这种干扰。
    """
    if args.file:
        raw = Path(args.file).read_bytes()
        text = raw.decode("utf-8-sig", errors="replace").strip()
        return text

    print("请粘贴 refresh_token 后回车（不会回显为明文）：")
    try:
        return sys.stdin.readline().strip()
    except KeyboardInterrupt:
        return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="百度网盘 refresh_token 诊断")
    parser.add_argument("--file", default="",
                        help="从文件读取令牌（避免管道编码干扰）")
    args = parser.parse_args()

    print("=" * 68)
    print("百度网盘 refresh_token 诊断")
    print("=" * 68)

    token = _read_token(args)
    if not token:
        print("❌ 没有读到内容。")
        return 2

    # ---------------- 先做本地体检：最常见的失败其实是"复制不完整/带了空白"
    print()
    print("--- 令牌本地体检 ---")
    print(f"  长度           : {len(token)}")
    print(f"  前 6 位        : {token[:6]}")
    print(f"  后 6 位        : {token[-6:]}")
    print(f"  含空白字符     : {'❌ 是（请重新复制）' if any(c.isspace() for c in token) else '✅ 否'}")
    print(f"  含非 ASCII     : {'❌ 是（多半是复制时混入了不可见字符）' if not token.isascii() else '✅ 否'}")
    print(f"  长度是否可疑   : "
          f"{'⚠ 偏短（百度令牌通常 50 字符以上）' if len(token) < 50 else '✅ 正常范围'}")

    # ---------------- 调在线 API，打印原始回应
    print()
    print("--- 调用 OpenList 在线 API ---")
    params = {"refresh_ui": token, "server_use": "true", "driver_txt": "baiduyun_go"}
    try:
        resp = requests.get(ENDPOINT, params=params, timeout=30)
    except requests.RequestException as exc:
        print(f"  ❌ 网络请求失败：{type(exc).__name__}: {exc}")
        print("     多半是网络问题，或 api.oplist.org 暂时不可用。")
        return 3

    print(f"  HTTP 状态      : {resp.status_code}")
    raw = (resp.text or "").strip()
    print(f"  原始回应       : {raw[:400]}")

    try:
        body = json.loads(raw)
    except ValueError:
        print()
        print("❌ 回应不是 JSON —— 端点或网络被劫持/拦截的可能性较大。")
        return 4

    access = str(body.get("access_token") or "")
    refresh = str(body.get("refresh_token") or "")
    text = str(body.get("text") or "")

    print()
    print("--- 判定 ---")
    if access and refresh:
        print("  ✅ 令牌有效！在线服务成功换到了新令牌。")
        print(f"     新 access_token 长度 : {len(access)}")
        print(f"     新 refresh_token 长度: {len(refresh)}")
        print()
        print("  ⚠️ 注意：百度**只保留最新**的 refresh_token。")
        print("     这里的调用也是一次「刷新」，所以 OpenList 里那份可能已作废。")
        print("     请回 https://api.oplist.org/ 重新获取一次，并**立刻**粘贴到 OpenList 保存。")
        return 0

    if text:
        print(f"  ❌ 百度/中转返回了具体错误：{text}")
        print()
        print("  常见原因：")
        print("    · 令牌已过期或被新的授权作废（百度只保留最新一个）")
        print("    · 令牌不是通过 https://api.oplist.org/ 拿的，")
        print("      用的是别的 app 凭据 → 这个中转服务刷不了它")
        return 5

    print("  ❌ 两个令牌都是空的，且没有错误文本 —— 与 OpenList 界面报的完全一致。")
    print()
    print("  最可能的三种原因（按概率排序）：")
    print("    1. 复制不完整 / 带了空格换行（先看上面的「本地体检」）")
    print("    2. 令牌已作废：百度只保留**最新**的 refresh_token，")
    print("       多次点「获取 Token」后，只有最后一次拿到的有效")
    print("    3. 令牌来自其他途径（不是 api.oplist.org 的「使用 OpenList 提供的参数」）")
    print()
    print("  建议：回 https://api.oplist.org/ 重新获取，复制后**立刻**保存到 OpenList，")
    print("        中间不要重复点击「获取 Token」。")
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
