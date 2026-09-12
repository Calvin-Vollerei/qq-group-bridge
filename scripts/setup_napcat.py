#!/usr/bin/env python3
"""安装并启动 NapCat，预置配置，并验证登录二维码可取得。

这个脚本把「部署 QQ 组件」这一整套动作自动化，也用于真机联调：

  1. 从压缩包安装 NapCat（已安装则跳过）
  2. 预置 ``config/webui.json`` 与 ``config/onebot11.json``
     （**关键**：NapCat 默认不开启 OneBot HTTP 服务，不预置就得让用户去
     网页端手点「新建 HTTP 服务器」）
  3. 启动 NapCat
  4. 等 WebUI 就绪
  5. 走已核对的接口取二维码，保存为 PNG
  6. 打印状态报告（含 OneBot 端口是否可用）

用法::

    python scripts/setup_napcat.py --zip .downloads/NapCat.Shell.Windows.Node.zip
    python scripts/setup_napcat.py --data-dir "dist/QQ群文件搬运工/data" --start
    python scripts/setup_napcat.py --verify-only          # 只验证当前状态
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qgb.config import NapCatConfig  # noqa: E402
from qgb.napcat.client import OneBotClient  # noqa: E402
from qgb.napcat.process import NapCatManager  # noqa: E402
from qgb.napcat.qr import check_login, fetch_qrcode  # noqa: E402
from _console import use_utf8_console  # noqa: E402  控制台切 UTF-8（Linux 上常是 cp1252）
from qgb.paths import resolve_data_dir  # noqa: E402


def main() -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="安装并启动 NapCat")
    parser.add_argument("--data-dir", default="", help="数据目录（默认自动解析）")
    parser.add_argument("--zip", default="", help="NapCat 压缩包路径（首次安装用）")
    parser.add_argument("--start", action="store_true", help="安装/预置后启动 NapCat")
    parser.add_argument("--verify-only", action="store_true", help="只验证，不做任何改动")
    parser.add_argument("--wait", type=float, default=90.0, help="等待 WebUI 就绪的秒数")
    parser.add_argument("--out", default="", help="二维码 PNG 输出路径")
    parser.add_argument("--api-base", default="http://127.0.0.1:3000")
    parser.add_argument("--webui-base", default="http://127.0.0.1:6099")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else resolve_data_dir()
    cfg = NapCatConfig(api_base=args.api_base, webui_base=args.webui_base)
    manager = NapCatManager(cfg, data_dir)
    out_png = Path(args.out) if args.out else (data_dir / "qrcode.png")

    print("=" * 68)
    print("NapCat 安装 / 启动 / 验证")
    print("=" * 68)
    print(f"数据目录 : {data_dir}")
    print(f"安装目录 : {manager.install_dir}")

    problems: list[str] = []

    # ---------------------------------------------------------- 1 安装
    if not args.verify_only:
        if manager.is_installed():
            print("[1/5] 已安装，跳过")
        elif args.zip:
            print(f"[1/5] 从 {args.zip} 安装…")
            try:
                manager.install_from_zip(args.zip)
                print("      安装完成")
            except Exception as exc:
                problems.append(f"安装失败：{exc}")
                print(f"      ❌ {exc}")
        else:
            print("[1/5] 未安装且未提供 --zip")
            print(manager.install_instructions())
            return 2
    else:
        print("[1/5] --verify-only，跳过安装")

    # ---------------------------------------------------------- 2 预置配置
    token = ""
    if not args.verify_only:
        print("[2/5] 预置配置…")
        try:
            info = manager.ensure_configs(
                webui_port=_port(args.webui_base, 6099),
                onebot_port=_port(args.api_base, 3000),
            )
            token = str(info.get("webui_token") or "")
            created = info.get("created") or []
            for path in created:
                print(f"      已写入 {Path(path).relative_to(manager.install_dir)}")
            if not created:
                print("      已有配置，保持不变")
            print(f"      WebUI 令牌：{token[:6]}…（已登记脱敏，不落日志）")
        except Exception as exc:
            problems.append(f"预置配置失败：{exc}")
            print(f"      ❌ {exc}")
    else:
        token = manager.read_webui_token_from_config()

    # ---------------------------------------------------------- 3 启动
    if args.start and not args.verify_only:
        print("[3/5] 启动 NapCat…")
        try:
            status = manager.start()
            print(f"      installed={status.installed} running={status.running} "
                  f"pid={status.pid}")
        except Exception as exc:
            problems.append(f"启动失败：{exc}")
            print(f"      ❌ {exc}")
    else:
        print("[3/5] 未要求启动（--start 可启用）")

    # ---------------------------------------------------------- 4 等就绪
    running = manager.is_running()
    if running:
        print(f"[4/5] 等待 WebUI 就绪（最多 {args.wait:.0f} 秒）…")
        deadline = time.time() + args.wait
        ready = False
        while time.time() < deadline:
            st = check_login(args.webui_base, token)
            if st.reachable:
                ready = True
                print(f"      ✅ WebUI 已就绪，QQ 状态：{st.message}")
                break
            time.sleep(2.5)
        if not ready:
            print("      ⚠ 超时未就绪（首次启动初始化 QQ NT 内核需要时间，可稍后重试）")
    else:
        print("[4/5] NapCat 未在运行，跳过就绪等待")

    # ---------------------------------------------------------- 5 二维码 + OneBot
    print("[5/5] 验证登录二维码与 OneBot 端口…")
    if running:
        qr = fetch_qrcode(args.webui_base, token, napcat_dir=manager.install_dir)
        if qr.ok:
            saved = qr.save(out_png)
            print(f"      ✅ 二维码已取得并渲染（来源 {qr.source}）")
            print(f"         二维码内容：{qr.qrcode_url[:60]}")
            print(f"         已保存到  ：{saved}")
        else:
            print(f"      ⚠ 二维码未取得：{qr.error}")
            if qr.hint:
                print(f"         建议：{qr.hint}")
            problems.append(f"二维码未取得：{qr.error}")

        client = OneBotClient(args.api_base)
        if client.ping():
            info = client.get_login_info()
            print(f"      ✅ OneBot HTTP 端口可用（{args.api_base}），登录状态：{info.message}")
        else:
            print(f"      ⚠ OneBot HTTP 端口暂不可用（{args.api_base}）")
            print("         未扫码登录前该端口可能不可用，属正常；扫码后再验证一次。")
    else:
        print("      NapCat 未运行，跳过")

    # ---------------------------------------------------------- 汇总
    print("=" * 68)
    st = manager.status()
    print(f"安装 : {'✅' if st.installed else '❌'}  {st.install_dir}")
    print(f"运行 : {'✅ pid=' + str(st.pid) if st.running else '⏹ 未运行'}")
    print(f"启动器: {st.launcher or '—'}")
    if problems:
        print(f"\n❌ 有 {len(problems)} 项需要处理：")
        for p in problems:
            print(f"   - {p}")
        return 1
    print("\n✅ 就绪：现在打开「QQ群文件搬运工」，到「QQ 登录」页点「获取二维码」即可扫码。")
    return 0


def _port(url: str, default: int) -> int:
    try:
        from urllib.parse import urlparse

        return int(urlparse(url).port or default)
    except (ValueError, TypeError):
        return default


if __name__ == "__main__":
    raise SystemExit(main())
