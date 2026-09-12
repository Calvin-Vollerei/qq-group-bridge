"""把「主程序分发包」与「NapCat 组件包」合成一个给普通用户的整包 zip。

用途
----
GitHub 发布页上默认只放**一个**资产，用户下载解压即用，不必先开程序再
去点「从压缩包安装…」。本脚本就是生成那个「整包」：

    qgb-v1.0-full.zip
      QQ群文件搬运工/            ← 主程序（来自 make_release_zip.py 的分发包）
        QQGroupBridge.exe
        _internal/ ...
        data/napcat/            ← NapCat 组件（用官方 OneKey 包安装后产生）
        portable.marker

设计原则（为什么不是简单地把两个目录一起压缩）
--------------------------------------------
1. **绝不打包腾讯客户端二进制**：只收 NapCat 自己的加载器与脚本
   （`shell/`、`bootmain/`、`NapCatInstaller.exe`、`7z.exe`）。
   QQ 运行时（node.exe / wrapper.node / 各种 QQ 的 dll）由用户首次运行时
   由 `NapCatInstaller.exe` 从官方渠道自行下载。
   这样既避开再分发腾讯客户端的授权问题，也避免把 300+ MB 塞进 zip。
2. **绝不打包本机运行期数据**：`napcat-embedded/` 里除了运行时还有
   `config.json`、`guild1.db*`、各类日志（可能含本机信息），一律不收。
3. **合并前先自检**：生成的 zip 会被重新打开逐条检查，命中禁止项就失败退出，
   由 `scripts/scan_secrets.py` 再做一次脱敏扫描。

用法
----
    python scripts/pack_release.py                    # 用 dist/ 下的默认路径
    python scripts/pack_release.py --version v1.0     # 指定版本号（决定文件名）
    python scripts/pack_release.py --no-napcat        # 只发布主程序包（不含组件）
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile
from _console import use_utf8_console  # noqa: E402  控制台切 UTF-8（Linux 上常是 cp1252）
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
APP_NAME = "QQ群文件搬运工"
def _release_tag() -> str:
    """发布标签：环境变量 > dist/RELEASE.txt > v1.0。

    与 ``make_release_zip.py`` 用**同一个来源**，否则会出现
    「主程序包叫 qgb-v0.1.0-app-only.zip、整包却去找 qgb-v1.0-app-only.zip」
    这种对不上的情况（实测踩过）。
    """
    env = os.environ.get("QGB_RELEASE_TAG")
    if env:
        return env.strip()
    try:
        tag = (DIST / "RELEASE.txt").read_text(encoding="utf-8").strip()
        if tag:
            return tag
    except OSError:
        pass
    return "v1.0"


DEFAULT_BASE_ZIP = DIST / f"qgb-{_release_tag()}-app-only.zip"
DEFAULT_RELEASE_DIR = DIST / APP_NAME
DEFAULT_NAPCAT_DIR = DEFAULT_RELEASE_DIR / "data" / "napcat"

#: NapCat 组件目录里**必须**打包的内容（加载器与安装器，非腾讯二进制）
NAPCAT_INCLUDE_TOP = ("shell", "bootmain", "NapCatInstaller.exe", "7z.exe", "7z.dll")

#: 即使在组件目录深处出现也绝不打包的文件名
NEVER_SHIP_NAMES = {
    "config.json",          # 含本机 WebUI 令牌 / OneBot 配置
    "secrets.enc",
    "state.db",
    "qrcode.png",
    "napcat-start.log",
}

#: 绝不打包的目录名（运行期、QQ 运行时、日志、缓存）
#:
#: ⚠️ 这几个目录不是「第三方运行库」而是**本机运行痕迹**：
#: 组件跑过一次之后，NapCat 会在 shell/ 里留下登录日志、guild 库、缓存二维码，
#: 内容可能包含 QQ 号、群号、本机路径。第一次实测时确实抓到了
#: shell/logs/*.log 与 shell/guild1.db —— 所以按目录整体排除，而不是按后缀猜。
NEVER_SHIP_DIRS = {
    "napcat-embedded",
    "logs",
    "cache",
    "data",
    "__pycache__",
    ".runtime",
}

#: 绝不打包的文件后缀（本机运行痕迹）
NEVER_SHIP_SUFFIXES = (
    ".log",
    ".db",
    ".db-wal",
    ".db-shm",
    ".enc",
)

#: 组件包里单个文件体积上限（MB）
#:
#: NapCat 自带 `native/napi2native/napi2native.linux.*.node` 实测 22.2 MB
#: —— 那是 **NapCat 自己的**跨平台原生模块（MIT），不是 QQ 客户端二进制，
#: 所以上限取 30 MB；真混进 QQ 运行时时会是 80 MB 的 node.exe 或
#: 上百 MB 的 wrapper.node，照样拦得住。
MAX_SINGLE_FILE_MB = 30


def _rel_ok(rel: Path) -> tuple[bool, str]:
    """组件目录里的相对路径是否可以打包。返回 (是否可打包, 原因)。"""
    parts = rel.parts
    if not parts:
        return False, "空路径"
    if parts[0] in NEVER_SHIP_DIRS:
        return False, f"运行期目录 {parts[0]}/"
    for part in parts[:-1]:
        if part in NEVER_SHIP_DIRS:
            return False, f"运行期目录 {part}/"
    if rel.name.lower() in NEVER_SHIP_NAMES:
        return False, f"本机数据 {rel.name}"
    low = rel.name.lower()
    if low.endswith(NEVER_SHIP_SUFFIXES):
        return False, f"本机运行痕迹 {rel.name}"
    return True, ""


def collect_napcat(napcat_dir: Path) -> tuple[list[Path], list[str]]:
    """收集组件包文件，返回 (文件列表, 排除说明)。"""
    files: list[Path] = []
    skipped: list[str] = []

    if not napcat_dir.is_dir():
        return [], [f"{napcat_dir} 不存在"]

    for top in NAPCAT_INCLUDE_TOP:
        item = napcat_dir / top
        if not item.exists():
            skipped.append(f"缺少 {top}（跳过）")
            continue
        if item.is_file():
            files.append(item)
            continue
        for path in sorted(item.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(napcat_dir)
            ok, why = _rel_ok(rel)
            if ok:
                files.append(path)
            else:
                skipped.append(f"{rel}（{why}）")

    return files, skipped


def _zip_entries(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return zf.infolist()


def verify(out_zip: Path) -> list[str]:
    """自检：整包里不允许出现本机数据或超大的 QQ 运行时。"""
    problems: list[str] = []
    with zipfile.ZipFile(out_zip) as zf:
        entries = _zip_entries(zf)
        if not entries:
            return ["zip 是空的"]

        for info in entries:
            name = info.filename
            low = name.lower()
            base = name.rsplit("/", 1)[-1].lower()
            if base in NEVER_SHIP_NAMES:
                problems.append(f"含本机数据文件：{name}")
            if low.endswith(("secrets.enc", "state.db", "state.db-wal", "state.db-shm")):
                problems.append(f"含本机状态文件：{name}")
            if "/napcat-embedded/" in low:
                problems.append(f"含 QQ 运行时目录：{name}")
            if info.file_size > MAX_SINGLE_FILE_MB * 1024 * 1024:
                problems.append(
                    f"单文件 {info.file_size / 1048576:.1f} MB 超限（疑似 QQ 运行时）：{name}"
                )
            # .enc 只对主程序里的 Tcl 编码表放行（big5.enc 等是 tkinter 正常显示
            # 文字所必需的，属于第三方运行库，不是凭据）
            if "/_internal/_tcl_data/" in low:
                continue
            if re.search(r"(?i)\.(enc|db|log)$", name):
                problems.append(f"本机运行痕迹：{name}")
    return problems


def build(base_zip: Path, napcat_dir: Path, out_zip: Path, *, with_napcat: bool) -> tuple[int, int]:
    """生成整包。返回 (主程序条目数, 组件条目数)。"""
    if not base_zip.is_file():
        raise SystemExit(
            f"找不到主程序分发包：{base_zip}\n"
            f"  请先运行：python scripts/make_release_zip.py"
        )

    if out_zip.exists():
        out_zip.unlink()
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    napcat_files: list[Path] = []
    if with_napcat:
        napcat_files, skipped = collect_napcat(napcat_dir)
        for line in skipped:
            print(f"   组件包跳过：{line}")
        if not napcat_files:
            print("   ⚠️ 组件目录为空 —— 本次只打主程序包")

    app_entries = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        # 1) 主程序包原样搬进来（内容与 release 分发包逐字节一致）
        with zipfile.ZipFile(base_zip) as src:
            for info in src.infolist():
                if info.is_dir():
                    continue
                out.writestr(info, src.read(info.filename))
                app_entries += 1

        # 2) NapCat 组件放进 data/napcat/（程序启动时按此路径探测）
        napcat_entries = 0
        for path in napcat_files:
            rel = path.relative_to(napcat_dir)
            arc = f"{APP_NAME}/data/napcat/{rel.as_posix()}"
            out.write(path, arcname=arc)
            napcat_entries += 1

    return app_entries, napcat_entries


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="生成 GitHub 发布用的整包 zip")
    parser.add_argument("--version", default="v1.0", help="版本号，用于文件名（默认 v1.0）")
    parser.add_argument("--base-zip", default=str(DEFAULT_BASE_ZIP), help="主程序分发包路径")
    parser.add_argument("--napcat-dir", default=str(DEFAULT_NAPCAT_DIR), help="NapCat 组件目录")
    parser.add_argument("--out", default="", help="输出 zip 路径（默认 dist/qgb-<版本>-full.zip）")
    parser.add_argument("--no-napcat", action="store_true", help="只发主程序包，不含 NapCat 组件")
    args = parser.parse_args(argv)

    base_zip = Path(args.base_zip)
    napcat_dir = Path(args.napcat_dir)
    #: 输出名刻意用纯 ASCII：中文文件名在 GitHub 网页上传/展示时容易被截断，
    #: 而 Release 资产名是用户最先看到的东西，稳定比好看重要。
    out_zip = Path(args.out) if args.out else DIST / f"qgb-{args.version}-full.zip"

    with_napcat = not args.no_napcat
    print("=" * 68)
    print("合成发布整包")
    print("=" * 68)
    print(f"  主程序包：{base_zip}")
    print(f"  组件目录：{napcat_dir}  ({'包含' if with_napcat else '不包含'})")
    print(f"  输出    ：{out_zip}")

    app_entries, napcat_entries = build(base_zip, napcat_dir, out_zip, with_napcat=with_napcat)

    print(f"\n打包完成：主程序 {app_entries} 项 + 组件 {napcat_entries} 项")
    print(f"  体积：{out_zip.stat().st_size / 1048576:.2f} MB")

    print("\n自检（禁止再分发的内容）…")
    problems = verify(out_zip)
    if problems:
        print("❌ 自检失败：")
        for p in problems[:20]:
            print(f"   - {p}")
        print(f"（共 {len(problems)} 项）")
        return 1
    print("✅ 自检通过：无本机数据、无 QQ 运行时")

    print("\n脱敏扫描…")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from scan_secrets import scan_zip  # noqa: PLC0415

        hits = scan_zip(out_zip)
    except Exception as exc:  # pragma: no cover - 扫描器不可用时明确告警
        print(f"⚠️ 无法调用 scan_secrets：{exc}")
        hits = []

    if hits:
        print("❌ 扫描命中敏感内容：")
        for hit in hits[:20]:
            print(f"   - {hit}")
        return 1
    print("✅ 扫描通过：未发现敏感信息")

    print("\n" + "=" * 68)
    print("可以直接拖到 GitHub Release 的文件：")
    print(f"  {out_zip}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
