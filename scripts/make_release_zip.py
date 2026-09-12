#!/usr/bin/env python3
"""生成可分发的发布包，并扫描它 —— 确保**发出去的东西**里没有凭据。

为什么必须单独做这一步
----------------------

``dist/<发布目录>/`` 里有一个 ``data\\`` 目录，那是**运行期数据**：

  * NapCat 安装（近百 MB，第三方文件）
  * 用户自己的 ``config.json``
  * ``secrets.enc`` —— DPAPI 加密的账号/令牌
  * ``state.db`` —— 搬运状态库
  * ``qrcode.png`` —— 登录二维码

它属于「本机的部署成果」，**绝不能随分发包发给朋友**。

直接在 ``dist\\`` 上跑扫密会怎样？实测：它把 NapCat 自带的第三方文件报成
三个误报（腾讯的 ``QQ-Team@tencent.com``、打包 JS 里的示例私钥），
于是真正的风险信号被淹没 —— 这种闸门其实是失效的。

正确做法：**按白名单把该发的东西打进一个 zip，再扫这个 zip**。
扫描对象 == 分发对象，闸门才真正有意义。本脚本就是这么做的。

用法::

    python scripts/make_release_zip.py
    python scripts/make_release_zip.py --release-dir "dist/QQ群文件搬运工" --out dist/分发包.zip
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from scan_secrets import scan_zip  # noqa: E402

#: 发布目录下**绝不分发**的顶层条目（运行期数据 / 本机凭据 / 构建残留）。
#: 这是主要机制 —— 便携模式下所有运行期状态都在 data\ 里。
RUNTIME_TOP_LEVEL = {
    "data",
    "logs",
    ".runtime",
    ".downloads",
    ".shots",
    "__pycache__",
}

#: 即使在别处出现也绝不分发的**确切文件名**（本机凭据与状态）。
#:
#: ⚠️ 这里是「确切名字」而不是「后缀」，是踩过坑后的选择：
#: 早先写成「排除 *.enc / *.db / *.log」，结果把 Tcl 运行库的编码表
#: （big5.enc、cp1252.enc …）也一起排除了 —— 那些是 tkinter 正常显示
#: 文字所必需的，剔掉会让打包后的程序出现编码问题。
#: 按确切名字匹配既保住了安全，又不会误伤第三方运行库。
NEVER_SHIP_NAMES = {
    "secrets.enc",
    "state.db",
    "state.db-wal",
    "state.db-shm",
    "config.json",     # 内含群号、路径等部署信息
    "qrcode.png",      # 登录二维码
}

#: 状态库的滚动文件形如 state.db-20260912；用前缀识别
NEVER_SHIP_PREFIXES = ("state.db",)

#: 例外：portable.marker 必须随包分发（它决定数据目录跟着程序走）
ALLOW = {"portable.marker"}


def should_ship(rel: Path) -> bool:
    """判断发布目录里的一个相对路径是否应该进入分发包。"""
    parts = rel.parts
    if not parts:
        return False

    # 主要机制：运行期目录整体不发
    if parts[0] in RUNTIME_TOP_LEVEL:
        return False

    name = rel.name.lower()
    if name in ALLOW:
        return True
    if name in NEVER_SHIP_NAMES:
        return False
    if any(name.startswith(prefix) for prefix in NEVER_SHIP_PREFIXES):
        return False
    return True


def build_zip(release_dir: Path, out_zip: Path) -> tuple[Path, int, list[str]]:
    """打包并返回 ``(zip 路径, 文件数, 被排除的顶层条目)``。"""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()

    count = 0
    excluded_top: set[str] = set()

    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(release_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(release_dir)
            if not should_ship(rel):
                if rel.parts:
                    excluded_top.add(rel.parts[0])
                continue
            zf.write(path, arcname=str(Path(release_dir.name) / rel))
            count += 1

    return out_zip, count, sorted(excluded_top)


def verify_no_runtime_leak(out_zip: Path) -> list[str]:
    """自检：分发包里**不允许**出现任何运行期路径或凭据文件。"""
    problems: list[str] = []
    with zipfile.ZipFile(out_zip) as zf:
        for info in zf.infolist():
            rel = Path(info.filename)
            parts = rel.parts[1:]  # 去掉最外层发布目录名
            if not parts:
                continue
            if parts[0] in RUNTIME_TOP_LEVEL:
                problems.append(f"包含运行期目录：{info.filename}")

            name = Path(info.filename).name.lower()
            if name in ALLOW:
                continue
            if name in NEVER_SHIP_NAMES or any(
                name.startswith(prefix) for prefix in NEVER_SHIP_PREFIXES
            ):
                problems.append(f"包含本机凭据/状态文件：{info.filename}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="生成可分发的发布包并扫描")
    parser.add_argument("--release-dir", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--skip-scan", action="store_true")
    args = parser.parse_args()

    release_dir = (
        Path(args.release_dir) if args.release_dir
        else ROOT / "dist" / "QQ群文件搬运工"
    )
    if not release_dir.is_dir():
        print(f"❌ 发布目录不存在：{release_dir}")
        return 2

    out_zip = Path(args.out) if args.out else ROOT / "dist" / "QQ群文件搬运工-分发包.zip"

    print("=" * 68)
    print("生成可分发包")
    print("=" * 68)
    print(f"发布目录 : {release_dir}")
    print(f"输出     : {out_zip}")

    out_zip, count, excluded = build_zip(release_dir, out_zip)
    size_mb = out_zip.stat().st_size / 1024 / 1024

    print()
    print(f"✅ 已打包 {count} 个文件，{size_mb:.1f} MB")
    if excluded:
        print(f"   已排除运行期/凭据条目：{', '.join(excluded)}")
        print("   （这些属于本机部署成果，随包发出去会泄露账号）")

    # ---------------- 自检：不许有运行期内容
    leaks = verify_no_runtime_leak(out_zip)
    if leaks:
        print()
        print(f"❌ 分发包里混入了 {len(leaks)} 项不该分发的内容：")
        for item in leaks[:10]:
            print(f"   - {item}")
        return 1
    print("✅ 自检通过：不含 data\\、logs\\、凭据库或状态库")

    # ---------------- 扫描分发包本身
    if not args.skip_scan:
        print()
        print("扫描分发包内容…")
        findings = scan_zip(out_zip)
        if findings:
            print(f"❌ 发现 {len(findings)} 处可疑内容：")
            for item in findings[:15]:
                print(f"   - {item}")
            print()
            print("分发包已生成，但**请勿分发**，先处理上述问题。")
            return 1
        print("✅ 扫描通过：分发包内未发现敏感信息")

    print()
    print("=" * 68)
    print("这个 zip 就是可以发给朋友的产物：")
    print(f"   {out_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
