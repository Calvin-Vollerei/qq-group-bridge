#!/usr/bin/env python3
"""启动 NapCat 挂钩（注入已安装的 QQ NT），并验证登录二维码可取得。

这是「让用户能直接扫码」的落地脚本。它把挂钩所需的全部细节固化下来：

  1. 预置 ``config/webui.json`` 与 ``config/onebot11.json``
     （NapCat 默认**不开启** OneBot HTTP 服务，不预置就得让用户去网页端点）
  2. 生成 ``loadNapCat.js``（用 Python 写，避免 cmd 的代码页损坏中文路径）
  3. 组装整套 ``NAPCAT_*`` 环境变量
  4. 启动 ``NapCatWinBootMain.exe <QQ.exe> <Hook.dll>``
  5. 等 WebUI 就绪，取二维码并保存

⚠️ **必须管理员权限**：挂钩是往 QQ 进程里注入 DLL，Windows 要求管理员，
否则会**静默失败**（没有任何报错，最难排查）。本脚本会提前检查并明确报错。

用法::

    # 普通运行（会检查权限）
    python scripts/start_napcat_hook.py --data-dir "dist/QQ群文件搬运工/data"

    # 自动提权（会弹 UAC）
    python scripts/start_napcat_hook.py --elevate

    # 只检查环境，不启动
    python scripts/start_napcat_hook.py --check
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qgb.config import NapCatConfig, load_config  # noqa: E402
from qgb.napcat.client import OneBotClient  # noqa: E402
from qgb.napcat.process import NapCatManager, is_admin, resolve_qq_path  # noqa: E402
from qgb.napcat.qr import check_login, fetch_qrcode  # noqa: E402
from qgb.paths import resolve_data_dir  # noqa: E402


def elevate_and_exit(argv: list[str]) -> int:
    """用 UAC 重新以管理员身份运行自己。

    交给 PowerShell 的 ``Start-Process -Verb RunAs`` 处理 —— 它会弹出 UAC，
    用户点「是」后新进程就带管理员令牌。
    """
    script = Path(__file__).resolve()
    args = [a for a in argv if a != "--elevate"]

    # PowerShell 数组字面量：单引号包裹，内部单引号翻倍转义
    quoted = ", ".join("'" + a.replace("'", "''") + "'" for a in [str(script), *args])
    ps = (
        f"$p = Start-Process -FilePath '{sys.executable}'"
        f" -ArgumentList @({quoted}) -Verb RunAs -PassThru -Wait;"
        " exit $p.ExitCode"
    )

    print("正在请求管理员权限（请在 UAC 弹窗中点「是」）…")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"❌ 提权失败：{exc}")
        return 1

    print(f"提权进程退出码：{proc.returncode}")
    if proc.returncode != 0:
        print("提示：若在 UAC 弹窗点了「否」，请重试并选择「是」。")
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 NapCat 挂钩并验证二维码")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--release", action="store_true",
                        help="使用发布包的数据目录（发布目录名含中文，"
                             "由 Python 解析可避免 .bat 的代码页问题）")
    parser.add_argument("--qq-path", default="", help="QQ.exe 路径（默认从注册表探测）")
    parser.add_argument("--elevate", action="store_true", help="自动用 UAC 提权")
    parser.add_argument("--check", action="store_true", help="只检查环境")
    parser.add_argument("--wait", type=float, default=90.0)
    parser.add_argument("--out", default="", help="二维码 PNG 输出路径")
    args = parser.parse_args()

    if args.elevate and not is_admin():
        return elevate_and_exit(sys.argv[1:])

    if args.data_dir:
        data_dir = Path(args.data_dir)
    elif args.release:
        # 发布目录名含中文（QQ群文件搬运工）。**绝不能把这个路径写进 .bat**：
        # cmd.exe 按系统代码页读取 .bat，UTF-8 的中文会被当成乱码命令执行。
        # 让 Python 自己拼路径就没有这个问题。
        data_dir = ROOT / "dist" / "QQ群文件搬运工" / "data"
    else:
        data_dir = resolve_data_dir()
    # 必须读**已保存的配置**，而不是新建一个默认配置：
    # napcat.install_dir / api_base / webui_base 都可能被用户或安装流程改过
    # （例如把 install_dir 指向自带运行时的 data\napcat-embedded）。
    # 新建默认配置会让这些设置被无声忽略 —— 这正是踩过的坑。
    config_path = data_dir / "config.json"
    if config_path.is_file():
        cfg = load_config(config_path).napcat
        if args.qq_path:
            cfg.qq_path = args.qq_path
    else:
        cfg = NapCatConfig(qq_path=args.qq_path)
    manager = NapCatManager(cfg, data_dir)
    out_png = Path(args.out) if args.out else (data_dir / "qrcode.png")

    print("=" * 68)
    print("NapCat 挂钩启动")
    print("=" * 68)
    print(f"数据目录   : {data_dir}")
    print(f"安装目录   : {manager.install_dir}")

    # 先判断用哪种方式启动 —— 这决定要不要管理员
    hook = manager.hook_layout()
    cmd, _cwd = manager.launcher_command()
    is_node_direct = bool(cmd) and cmd[0].lower().endswith("node.exe")
    admin = is_admin()

    if is_node_direct:
        print("启动方式   : 自带运行时（node 直调）—— **不需要管理员**")
        print("            不挂钩你的 QQ，也不受你 QQ 版本影响")
    elif hook is not None:
        print("启动方式   : 挂钩已安装的 QQ NT —— **需要管理员权限**")
    else:
        print("启动方式   : 未识别")

    print(f"管理员权限 : {'✅ 是' if admin else '否'}"
          + ("（本模式不需要）" if is_node_direct else ""))

    qq_path = resolve_qq_path(args.qq_path) if hook is not None else ""
    if hook is not None:
        print(f"QQ 入口    : {qq_path or '❌ 未找到（注册表里也没有）'}")

    if args.check:
        print("=" * 68)
        if is_node_direct:
            return 0
        return 0 if (hook and qq_path and admin) else 1

    if not cmd:
        print()
        print("❌ 没有可用的启动方式。请确认安装目录里包含：")
        print("   · 自带运行时：node.exe + index.js")
        print(f"   · 或挂钩模式：shell/NapCatWinBootMain.exe")
        print(f"   安装目录当前为：{manager.install_dir}")
        return 2

    # 只有挂钩模式才需要管理员
    if hook is not None and not is_node_direct and not admin:
        print()
        print("❌ 需要管理员权限。")
        print("   NapCat 要把 Hook 注入 QQ 进程，Windows 要求管理员；")
        print("   否则注入会**静默失败**（界面毫无反应，没有任何报错）。")
        print()
        print("   请用以下任一方式重试：")
        print("     · python scripts/start_napcat_hook.py --elevate")
        print("     · 右键「以管理员身份启动.bat」→ 以管理员身份运行")
        return 3

    if hook is not None and not is_node_direct and not qq_path:
        print()
        print("❌ 没找到 QQ.exe。请用 --qq-path 指定，例如 --qq-path D:\\QQ\\QQ.exe")
        return 4

    # ---------------- 预置配置与负载
    print()
    print("[1/3] 预置配置…")
    info = manager.ensure_configs()
    for created in info.get("created") or []:
        print(f"      已写入 {Path(created).relative_to(manager.install_dir)}")
    if not info.get("created"):
        print("      已有配置，保持不变")
    token = str(info.get("webui_token") or "")

    # ---------------- 启动
    print("[2/3] 启动挂钩…")
    try:
        status = manager.start()
    except Exception as exc:
        print(f"      ❌ {exc}")
        hint = getattr(exc, "hint", "")
        if hint:
            print(f"      {hint}")
        return 5
    print(f"      ✅ pid={status.pid}")

    # ---------------- 等就绪 + 取二维码
    print(f"[3/3] 等待 WebUI 就绪（最多 {args.wait:.0f} 秒）…")
    deadline = time.time() + args.wait
    ready = False
    while time.time() < deadline:
        st = check_login(cfg.webui_base, token)
        if st.reachable:
            ready = True
            print(f"      ✅ WebUI 已就绪，QQ 状态：{st.message}")
            break
        time.sleep(2.5)

    result = 0
    if ready:
        qr = fetch_qrcode(cfg.webui_base, token, napcat_dir=manager.install_dir)
        if qr.ok:
            saved = qr.save(out_png)
            print(f"      ✅ 二维码已生成 → {saved}")
            print(f"         内容：{qr.qrcode_url[:70]}")
        else:
            print(f"      ⚠ 二维码未取得：{qr.error}")
            if qr.hint:
                print(f"         {qr.hint}")
            result = 6

        client = OneBotClient(cfg.api_base)
        print(f"      OneBot 端口（{cfg.api_base}）："
              f"{'✅ 可用' if client.ping() else '⚠ 未就绪（扫码登录后会可用）'}")
    else:
        print("      ⚠ 超时未就绪。可查看：")
        print(f"         {manager.install_dir / 'shell' / 'debug.log'}")
        print(f"         {manager.log_dir}")
        result = 7

    print("=" * 68)
    if result == 0:
        print("✅ 就绪：打开「QQ群文件搬运工」→「QQ 登录」→「获取二维码」即可扫码。")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
