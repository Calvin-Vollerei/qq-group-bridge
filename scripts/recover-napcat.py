"""一键恢复 QQ 组件（NapCat）：修限流 → 对齐令牌 → 拉起并验证到能扫码。

用于这两种症状（本机实测过）::

    WebUI 令牌无效          ← 程序凭据库里的令牌与组件配置里的不一致
    login rate limit        ← 反复点「获取二维码」撞上 NapCat 的限流
    QQ 状态：无法连接 QQ 组件（ConnectionError）  ← 组件进程根本没起

它做四件事：

1. 若组件在运行，先停掉（重启才能清掉限流状态）
2. 读组件配置里的**真实令牌**，并用 ``ensure_configs`` 把
   ``loginRate`` 修正为 10（NapCat 默认 3，对配置期太紧），
   同时让程序凭据库里的令牌与之一致 —— 这就是「WebUI 令牌无效」的根因
3. 用与界面「启动组件」完全相同的路径启动，并等到 WebUI 端口就绪
4. 直接查一次登录状态；未登录就取一张二维码存成 PNG，并打印出文件路径

用法（**先关掉搬运工程序**，否则端口会冲突）::

    python scripts/recover-napcat.py                     # 用发布目录里的组件
    python scripts/recover-napcat.py --data-dir <目录>    # 指定数据目录
    python scripts/recover-napcat.py --no-qr             # 只启动，不取二维码
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _console import use_utf8_console  # noqa: E402

from qgb.config import NapCatConfig, load_config  # noqa: E402
from qgb.errors import QgbError  # noqa: E402
from qgb.napcat import qr as qr_mod  # noqa: E402
from qgb.napcat.process import NapCatManager, is_admin  # noqa: E402
from qgb.secrets import KEY_NAPCAT_WEBUI_TOKEN, SecretStore, default_store_path  # noqa: E402


def _read_webui_json(config_root: Path) -> dict:
    """读 ``<config_root>/config/webui.json``。

    ⚠️ ``config_root()`` 返回的是 ``…/napcat`` 这一层，WebUI 配置在它下面的
    ``config/`` 子目录里。第一版漏了这层，于是永远读到空字符串 —— 于是
    ``old_token`` 恒为空、脚本每次都让 ensure_configs **重新生成**令牌，
    用户切回界面点「启动组件」时就又变成不一致（又报「WebUI 令牌无效」）。
    这种"读不到就静默用默认值"的写法是本次的教训：读配置失败必须能看出来。
    """
    path = Path(config_root) / "config" / "webui.json"
    if not path.is_file():
        return {}
    try:
        import json
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_config_token(config_root: Path) -> str:
    return str(_read_webui_json(config_root).get("token") or "")
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("token") or "")


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="恢复 QQ 组件（修限流 / 对齐令牌 / 拉起）")
    parser.add_argument("--data-dir", default="", help="数据目录（默认用发布目录里的 data/）")
    parser.add_argument("--webui-port", type=int, default=6099)
    parser.add_argument("--onebot-port", type=int, default=3000)
    parser.add_argument("--wait", type=float, default=60.0, help="等待 WebUI 就绪的最长秒数")
    parser.add_argument("--no-qr", action="store_true", help="只启动，不获取二维码")
    parser.add_argument("--keep-running", action="store_true", help="结束后不停止组件")
    args = parser.parse_args(argv)

    print("=" * 66)
    print("恢复 QQ 组件（NapCat）")
    print("=" * 66)

    # ---------------- 数据目录与配置
    if args.data_dir:
        data_dir = Path(args.data_dir).resolve()
    else:
        # 优先用发布目录（用户实际在跑的那份）
        candidates = [
            ROOT / "dist" / "QQ群文件搬运工" / "data",
            ROOT / "data",
        ]
        data_dir = next((c for c in candidates if c.is_dir()), candidates[0])
    print(f"  数据目录：{data_dir}")

    cfg_path = data_dir / "config.json"
    if cfg_path.is_file():
        cfg = load_config(cfg_path)
        napcat_cfg = cfg.napcat
        print(f"  使用配置：{cfg_path}")
    else:
        napcat_cfg = NapCatConfig()
        print(f"  未找到 {cfg_path}，使用默认组件配置")

    napcat_cfg.webui_port = args.webui_port
    napcat_cfg.onebot_port = args.onebot_port

    log_dir = data_dir / "logs"
    mgr = NapCatManager(napcat_cfg, data_dir, log_dir=log_dir)
    print(f"  安装目录：{mgr.install_dir}")
    print(f"  布局    ：{'自带运行时（无需管理员）' if (mgr.install_dir / 'node.exe').is_file() else '挂钩模式（需要管理员）'}")
    if not mgr.is_installed():
        print("\n❌ 组件未安装：安装目录里找不到 NapCat")
        return 2

    problems = mgr.embedded_runtime_problems()
    if problems:
        print("\n⚠️ 自带运行时缺文件：")
        for p in problems:
            print(f"     - {p}")

    # ---------------- 1. 停掉在跑的实例（重启才能清限流）
    print("\n[1/4] 检查并停止旧进程…")
    if mgr.is_running():
        print("      组件在运行，先停止（重启才能清掉限流状态）")
        mgr.stop()
        time.sleep(3)
    else:
        print("      组件未在运行")
    if mgr.is_running():
        print("      ⚠️ 仍然检测到端口占用，可能被别的程序占着")

    # ---------------- 2. 对齐令牌 + 修限流
    print("\n[2/4] 对齐令牌与限流设置…")
    old_token = _read_config_token(mgr.config_root())
    # ⚠️ 必须把**已有令牌原样传回去**。ensure_configs 在 force_webui_token=True
    #    且不传 token 时会 os.urandom 生成一个**全新**令牌 —— 那样每次运行本脚本
    #    都会换掉令牌，用户切回界面「启动组件」时又变成不一致、又报「令牌无效」。
    #    正确做法：读旧值 → 原样写回（顺便把 loginRate 修正为 10）。
    created = mgr.ensure_configs(
        webui_token=old_token or None,
        webui_port=args.webui_port,
        onebot_port=args.onebot_port,
        force_webui_token=True if old_token else False,
    )
    new_token = _read_config_token(mgr.config_root())
    same = (old_token == new_token and bool(new_token))
    print(f"      组件配置令牌：{old_token[:8] + '…' if old_token else '（原本为空）'}"
          f"  ->  {new_token[:8] + '…' if new_token else '（读取失败）'}"
          f"   {'保持原值不变 ✅' if same else '已更新'}")
    print(f"      重写文件：{len(created)} 个")

    webui = _read_webui_json(mgr.config_root())
    print(f"      loginRate 现为：{webui.get('loginRate')}（NapCat 默认 3，本工具放宽到 10）")
    if not webui:
        print("      ⚠️ 读不到 webui.json，请确认组件已安装且配置目录可读")

    # 让程序凭据库里的令牌与组件配置一致 —— 「WebUI 令牌无效」的根因
    try:
        store_path = default_store_path()
        store = SecretStore(store_path)
        store.set(KEY_NAPCAT_WEBUI_TOKEN, new_token)
        print(f"      ✅ 已把凭据库里的令牌同步为组件里的值（{store_path}）")
    except Exception as exc:  # noqa: BLE001
        print(f"      ⚠️ 无法写入凭据库：{type(exc).__name__}: {exc}")
        print("         （界面仍会以组件配置为准，通常不影响使用）")

    # ---------------- 3. 启动
    print("\n[3/4] 启动组件…")
    if is_admin():
        print("      当前是管理员权限")
    uses_embedded = (mgr.install_dir / "node.exe").is_file()
    if not uses_embedded and mgr.hook_layout() is not None and not is_admin():
        print("      ❌ 挂钩模式需要管理员权限：请用「以管理员身份启动.bat」重试")
        return 3
    try:
        status = mgr.start(wait_ready=0.0)
        print(f"      已发起启动：{status.message if hasattr(status, 'message') else status}")
    except QgbError as exc:
        print(f"      ❌ 启动失败：{exc.message}")
        if getattr(exc, "hint", ""):
            for line in str(exc.hint).splitlines():
                print(f"         {line}")
        return 4

    # 等端口就绪
    deadline = time.time() + args.wait
    ready = False
    while time.time() < deadline:
        if mgr.is_running():
            ready = True
            break
        time.sleep(1.5)
    if ready:
        print(f"      ✅ WebUI 端口 {args.webui_port} 已就绪")
    else:
        print(f"      ❌ 等了 {args.wait:.0f} 秒端口仍未就绪 —— 组件可能启动后立刻退出")
        for name in ("napcat.out.log", "napcat.err.log"):
            p = log_dir / name
            if p.is_file() and p.stat().st_size:
                print(f"      --- {name} 尾部")
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]:
                    print(f"          {line}")

    # ---------------- 4. 登录状态 / 二维码
    if not args.no_qr:
        base = f"http://127.0.0.1:{args.webui_port}"
        print("\n[4/4] 查询登录状态…")
        st = qr_mod.check_login(base, new_token)
        state = "已登录" if st.is_login else ("已掉线" if st.is_offline else "未登录")
        print(f"      状态：{state}   {st.message}")
        if not st.is_login:
            print("      正在获取二维码…")
            code = qr_mod.fetch_qrcode(base, new_token, napcat_dir=mgr.config_root())
            ok = bool(code.png_bytes)
            print(f"      结果：{'成功' if ok else '失败'}   {code.error or code.source}")
            if ok:
                out = data_dir / "qrcode.png"
                out.write_bytes(code.png_bytes)
                print(f"\n  ✅ 二维码已保存：{out}")
                print("     用手机 QQ（小号）扫码；扫完回到程序点「获取二维码」或重启程序即可。")
            elif code.hint:
                print(f"      提示：{code.hint}")

    print("\n" + "=" * 66)
    if args.keep_running:
        print("组件保持运行中（未停止）。")
    else:
        print("组件仍在运行中。若想停掉：在程序里点「停止组件」，或直接结束 node.exe / NapCat 进程。")
    print("接下来：打开搬运工 →「QQ 登录」页确认状态为已登录 →「监控」页点「开始监控」")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
