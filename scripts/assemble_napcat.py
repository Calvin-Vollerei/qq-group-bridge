#!/usr/bin/env python3
"""离线组装 NapCat 无头运行时。

背景（这是真机踩出来的，不是猜测）：

  NapCat 官方「一键绿色版」的 ``NapCatInstaller.exe`` 需要下载 **QQ NT 客户端**，
  但它里面**写死了一个固定版本的下载地址**。腾讯会定期更换版本与 CDN 路径，
  于是那个地址会变成 404，安装器就永远卡在「下载 QQ 失败」。
  （本机实测：安装器内置的
  ``dldir1.qq.com/qqfile/qq/QQNT/be71d851/QQ9.9.26.44498_x64.exe`` 已 404。）

  官方的新地址在
  ``https://cdn-go.cn/qq-web/im.qq.com_new/latest/rainbow/windowsConfig.js``
  里，但 ``qqdl.gtimg.cn/qqfile/QQNTV2/...`` 对非浏览器请求返回 403。

所以本脚本负责「离线组装」：你**用浏览器**下载好 QQ 安装包，
剩下的抽取、拼装、写配置、启动全部自动完成 —— 而且做成**便携副本**，
不会动你已经装好的 QQ / TIM。

用法::

    # 用浏览器下载的 QQ 安装包 + 已下好的 NapCat.Shell.zip 组装
    python scripts/assemble_napcat.py \
        --qq-installer "%USERPROFILE%\\Downloads\\QQ_9.9.35_260902_x64_01.exe" \
        --napcat-shell .downloads/NapCat.Shell.zip \
        --data-dir "dist/QQ群文件搬运工/data" \
        --start

    # 只查看当前探测到的布局（不写任何东西）
    python scripts/assemble_napcat.py --inspect
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qgb.config import NapCatConfig  # noqa: E402
from qgb.napcat.process import NapCatManager  # noqa: E402
from qgb.paths import resolve_data_dir  # noqa: E402


def find_7z(manager: NapCatManager) -> Path | None:
    """优先用 NapCat 自带包里的 7z.exe（不依赖系统安装 7-Zip）。"""
    for candidate in (
        manager.install_dir / "7z.exe",
        manager.install_dir / "bootmain" / "7z.exe",
    ):
        if candidate.is_file():
            return candidate
    found = shutil.which("7z") or shutil.which("7za")
    return Path(found) if found else None


def extract_installer(seven_zip: Path, installer: Path, dest: Path) -> bool:
    """把 QQ 安装包当成压缩包解开（它是自解压包，7z 能直接读）。"""
    dest.mkdir(parents=True, exist_ok=True)
    print(f"      用 {seven_zip.name} 解包 {installer.name} …")
    try:
        proc = subprocess.run(
            [str(seven_zip), "x", str(installer), f"-o{dest}", "-y"],
            capture_output=True, text=True, timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"      ❌ 解包失败：{exc}")
        return False

    if proc.returncode != 0:
        tail = (proc.stdout or proc.stderr or "").strip().splitlines()[-6:]
        print("      ❌ 7z 返回非零：")
        for line in tail:
            print(f"         {line}")
        return False
    return True


def locate_qq_root(search: Path) -> Path | None:
    """找到含 QQ.exe 的目录（即便携 QQ 的根）。"""
    for candidate in [search, *[p for p in search.iterdir() if p.is_dir()]]:
        if (candidate / "QQ.exe").is_file():
            return candidate
    for qq in search.rglob("QQ.exe"):
        return qq.parent
    return None


def locate_app_dir(qq_root: Path) -> Path | None:
    """找到 ``versions/<ver>/resources/app``（NapCat 要放进去的地方）。"""
    pattern = "versions/*/resources/app"
    for path in sorted(qq_root.glob(pattern)):
        if path.is_dir():
            return path
    # 有些打包方式没有 versions 层
    direct = qq_root / "resources" / "app"
    if direct.is_dir():
        return direct
    for path in qq_root.rglob("resources/app"):
        if path.is_dir():
            return path
    return None


def install_napcat_payload(shell_zip: Path, app_dir: Path) -> Path:
    """把 NapCat.Shell.zip 解到 ``resources/app/napcat``。"""
    target = app_dir / "napcat"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(shell_zip) as zf:
        # 压缩包里可能套了一层同名目录，剥掉它
        names = zf.namelist()
        prefix = ""
        if names and all(n.startswith("napcat/") for n in names if n.strip()):
            prefix = "napcat/"
        members = [n for n in names if n.strip() and not n.endswith("/")]
        for name in members:
            rel = name[len(prefix):] if prefix and name.startswith(prefix) else name
            if not rel:
                continue
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="离线组装 NapCat 无头运行时")
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--qq-installer", default="", help="浏览器下载的 QQ 安装包")
    parser.add_argument("--napcat-shell", default="", help="NapCat.Shell.zip")
    parser.add_argument("--start", action="store_true", help="组装完直接启动")
    parser.add_argument("--inspect", action="store_true", help="只探测布局，不做改动")
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else resolve_data_dir()
    manager = NapCatManager(NapCatConfig(), data_dir)

    print("=" * 70)
    print("NapCat 离线组装")
    print("=" * 70)
    print(f"数据目录 : {data_dir}")
    print(f"安装目录 : {manager.install_dir}")

    # ---------------------------------------------------------- 探测
    if args.inspect or not (args.qq_installer and args.napcat_shell):
        print()
        print("当前布局探测：")
        qq_exe = manager.install_dir / "QQ.exe"
        print(f"  QQ.exe            : {'✅ ' + str(qq_exe) if qq_exe.is_file() else '❌ 未找到'}")
        qq_root = locate_qq_root(manager.install_dir) if manager.install_dir.is_dir() else None
        print(f"  QQ 根目录         : {qq_root or '❌ 未找到'}")
        app_dir = locate_app_dir(qq_root) if qq_root else None
        print(f"  resources/app     : {app_dir or '❌ 未找到'}")
        apps = manager._napcat_app_dirs()
        print(f"  NapCat 应用目录   : {apps[0] if apps else '❌ 未找到'}")
        print(f"  配置写入位置      : {manager.config_root()}")
        print(f"  启动器            : {manager.launcher_path() or '❌ 未找到'}")
        seven = find_7z(manager)
        print(f"  7z                : {seven or '❌ 未找到（需要 NapCat 包内的 7z.exe）'}")
        print(f"  NapCat.Shell.zip  : {args.napcat_shell or '（未提供）'}")
        print(f"  QQ 安装包         : {args.qq_installer or '（未提供）'}")

        if not (args.qq_installer and args.napcat_shell):
            print()
            print("提示：要开始组装，请同时提供 --qq-installer 与 --napcat-shell。")
            print("      QQ 安装包请用浏览器从 im.qq.com 下载（自动请求会被 CDN 403）。")
            return 0
        return 0

    installer = Path(args.qq_installer).expanduser()
    shell_zip = Path(args.napcat_shell).expanduser()
    problems: list[str] = []

    for label, path in (("QQ 安装包", installer), ("NapCat.Shell.zip", shell_zip)):
        if not path.is_file():
            problems.append(f"{label}不存在：{path}")

    seven = find_7z(manager)
    if seven is None:
        problems.append(
            "找不到 7z.exe。请先把 NapCat.Shell.Windows.OneKey.zip 解压到安装目录"
            "（它自带 7z.exe），或安装 7-Zip 并加入 PATH。"
        )

    if problems:
        print()
        for p in problems:
            print(f"  ❌ {p}")
        return 2

    # ---------------------------------------------------------- 解包 QQ
    print()
    print("[1/4] 解包 QQ 便携运行时…")
    work = manager.install_dir / "_qq_extract"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    if not extract_installer(seven, installer, work):
        return 1

    qq_root = locate_qq_root(work)
    if qq_root is None:
        print("      ❌ 解包后没找到 QQ.exe，QQ 安装包结构可能不同。")
        print(f"         请把 {work} 的内容反馈给维护者。")
        return 1
    print(f"      ✅ QQ 运行时：{qq_root}")

    app_dir = locate_app_dir(qq_root)
    if app_dir is None:
        print("      ❌ 没找到 resources/app 目录。")
        print(f"         目录结构：{[p.name for p in qq_root.iterdir()][:20]}")
        return 1
    print(f"      ✅ 应用目录：{app_dir}")

    # 把便携运行时挪到安装目录根部（OneKey 的布局就是 QQ.exe 在根）
    print("      → 搬到安装目录根部…")
    for item in qq_root.iterdir():
        target = manager.install_dir / item.name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        shutil.move(str(item), str(target))
    shutil.rmtree(work, ignore_errors=True)

    qq_root = manager.install_dir
    app_dir = locate_app_dir(qq_root)
    if app_dir is None:
        print("      ❌ 搬移后找不到 resources/app")
        return 1
    print(f"      ✅ QQ 运行时已就位：{qq_root / 'QQ.exe'}")

    # ---------------------------------------------------------- 装 NapCat
    print()
    print("[2/4] 安装 NapCat 负载到 resources/app/napcat …")
    payload = install_napcat_payload(shell_zip, app_dir)
    count = sum(1 for _ in payload.rglob("*") if _.is_file())
    print(f"      ✅ 已写入 {count} 个文件 → {payload}")

    # ---------------------------------------------------------- 写配置
    print()
    print("[3/4] 预置配置…")
    info = manager.ensure_configs()
    for path in info.get("created") or []:
        print(f"      ✅ {Path(path).relative_to(manager.install_dir)}")
    if not info.get("created"):
        print("      已有配置，保持不变")

    # ---------------------------------------------------------- 完成
    print()
    print("[4/4] 结果")
    print(f"      安装目录 : {manager.install_dir}")
    print(f"      启动器   : {manager.launcher_path() or '未识别'}")
    print(f"      配置位置 : {manager.config_root() / 'config'}")

    if args.start:
        print()
        print("      启动中…")
        try:
            status = manager.start()
            print(f"      ✅ 已启动 pid={status.pid}")
        except Exception as exc:
            print(f"      ❌ 启动失败：{exc}")

    print("=" * 70)
    print("下一步：打开「QQ群文件搬运工」→「QQ 登录」→「获取二维码」扫码。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
