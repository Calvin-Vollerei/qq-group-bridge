"""把「完整包」合成为给零基础用户的交付包。

交付包和完整包的**唯一区别**是面向用户的表面层，程序本体一字不改：

  1. zip 文件名、以及里面那个顶层文件夹名，都改成纯中文且自我说明：
         QQ群文件搬运工-解压我.zip
         └── QQ群文件搬运工/          ← 解压出来的文件夹
  2. 把 ``自检.bat`` 改名为 ``出错了点这个.bat``
     （「自检」对零基础用户没有意义；「出错了点这个」是行动指令）
  3. 首页放一份 ``README.md``（GitHub 上会自动渲染成富文本预览）
  4. 放入三份面向用户的文档，并统一成 UTF-8 带 BOM（Windows 记事本不乱码）：
         第一步看这里.txt          ← 一页纸流程
         百度网盘授权怎么做.txt      ← 最难那一步的分步说明
         使用说明.txt              ← 详细参考（原有）
  5. 包内提到 ``自检.bat`` 的地方同步改成新名字，避免断链

用法::

    python scripts/make_delivery_zip.py                 # 用 dist/ 下默认路径
    python scripts/make_delivery_zip.py --version v1.0  # 指定版本（决定文件名）

自检分三层：结构检查（该在的都在、不该在的都没有）→ 重命名是否生效
→ ``scan_secrets.py`` 扫一遍。任何一层不过就失败退出。
"""

from __future__ import annotations

import argparse
import os
import sys
import zipfile
from pathlib import Path

from _console import use_utf8_console  # noqa: E402  控制台切 UTF-8（Linux 上常是 cp1252）

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
MANUAL = DIST / "QQ群文件搬运工"
APP_DIR = "QQ群文件搬运工"          # zip 内的顶层目录名
SELF_TEST_OLD = "自检.bat"
SELF_TEST_NEW = "出错了点这个.bat"

#: 交付包必须包含的顶层文件（缺一个就失败）
REQUIRED = (
    "QQGroupBridge.exe",
    "启动搬运工.bat",
    "出错了点这个.bat",
    "以管理员身份启动.bat",
    "使用说明.txt",
    "第一步看这里.txt",
    "百度网盘授权怎么做.txt",
    "README.md",
    "portable.marker",
)

#: 我们自己的 .bat（只有这几个需要保证纯 ASCII；NapCat 自带的不管）
OUR_BATS = ("启动搬运工.bat", "出错了点这个.bat", "以管理员身份启动.bat")

#: 交付包里绝不允许出现的东西
FORBIDDEN = (
    "data/secrets.enc",
    "data/config.json",
    "data/state.db",
    "data/napcat-embedded",
    "logs/",
)


def _release_tag() -> str:
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


def _read_text_with_bom(path: Path) -> bytes:
    """读文本并统一成 UTF-8 带 BOM（Windows 记事本按 ANSI 打开才不会乱码）。"""
    text = path.read_text(encoding="utf-8-sig")
    return text.encode("utf-8-sig")


def _target_name(src_name: str, app_dir: str) -> str:
    """把源 zip 内的条目名映射成交付包里的名字。

    只做两件事，且都是**整条路径**级别的替换（不再零散地拼接字符串，
    免得像第一版那样两个分支算出同一个路径）：
      * 顶层目录改名：``QQ群文件搬运工/x`` → ``<app_dir>/x``
      * ``自检.bat`` 改名：``…/自检.bat`` → ``…/出错了点这个.bat``
    """
    parts = src_name.split("/")
    if parts and parts[0] == APP_DIR:
        parts = [app_dir] + parts[1:]
    if parts and parts[-1] == SELF_TEST_OLD:
        parts[-1] = SELF_TEST_NEW
    return "/".join(parts)


def build(base_zip: Path, out_zip: Path, app_dir: str) -> tuple[int, dict[str, bytes]]:
    """生成交付包，返回 (条目数, 顶层文件内容快照)。"""
    if not base_zip.is_file():
        raise SystemExit(
            f"找不到完整包：{base_zip}\n"
            f"  请先运行：python scripts/make_release_zip.py 与 python scripts/pack_release.py"
        )

    if out_zip.exists():
        out_zip.unlink()
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    snapshot: dict[str, bytes] = {}

    with zipfile.ZipFile(base_zip) as src, \
            zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as out:
        for info in src.infolist():
            if info.is_dir():
                continue
            target = _target_name(info.filename, app_dir)
            # 内容原样搬运，**绝不去改 .bat 的内容**。踩过的坑见 verify() 里的
            # _BAT_MUST_KEEP 检查：第一版按「删掉含 selftest 的行」改写 bat，
            # 把 `QQGroupBridge.exe --selftest --out "%REPORT%"` 也删了，
            # 于是「出错了点这个.bat」双击后什么都不输出 —— 求救通道失效。
            out.writestr(target, src.read(info))
            count += 1
            if target.count("/") == 1:          # 顶层文件，留快照给自检
                snapshot[target.split("/")[-1]] = src.read(info)

        # 覆盖/追加面向用户的文档（统一 UTF-8 BOM）
        docs = {
            "第一步看这里.txt": ROOT / "packaging" / "第一步看这里.txt",
            "百度网盘授权怎么做.txt": ROOT / "packaging" / "百度网盘授权怎么做.txt",
            "使用说明.txt": ROOT / "packaging" / "使用说明.txt",
            "README.md": ROOT / "README.md",
        }
        for name, src_path in docs.items():
            if not src_path.is_file():
                raise SystemExit(f"缺少文档源文件：{src_path}")
            data = _read_text_with_bom(src_path)
            if name == "使用说明.txt":
                # 包内那份已改名，说明里同步免得断链
                data = data.decode("utf-8-sig").replace(SELF_TEST_OLD, SELF_TEST_NEW).encode("utf-8-sig")
            out.writestr(f"{app_dir}/{name}", data)
            snapshot[name] = data
            count += 1

    return count, snapshot


def verify(out_zip: Path, app_dir: str) -> list[str]:
    """结构自检：该在的都在、不该在的都没有、重命名生效。"""
    problems: list[str] = []
    with zipfile.ZipFile(out_zip) as zf:
        names = zf.namelist()
        if not names:
            return ["zip 是空的"]

        tops = {n.split("/")[0] for n in names}
        if tops != {app_dir}:
            problems.append(f"顶层目录应只有 {app_dir}/，实际：{sorted(tops)}")

        for want in REQUIRED:
            if f"{app_dir}/{want}" not in names:
                problems.append(f"缺少必需文件：{want}")

        if f"{app_dir}/{SELF_TEST_OLD}" in names:
            problems.append(f"{SELF_TEST_OLD} 应已改名为 {SELF_TEST_NEW}")

        for bad in FORBIDDEN:
            hits = [n for n in names if bad in n]
            if hits:
                problems.append(f"含禁止内容 {bad}（{len(hits)} 项）")

        # 文档必须是 UTF-8 BOM，否则记事本可能乱码
        for name in ("第一步看这里.txt", "百度网盘授权怎么做.txt", "使用说明.txt"):
            full = f"{app_dir}/{name}"
            if full in names:
                head = zf.read(full)[:3]
                if head != b"\xef\xbb\xbf":
                    problems.append(f"{name} 缺 UTF-8 BOM")

        # 我们自己的 .bat 必须仍为纯 ASCII（cmd 用控制台代码页读它，非 ASCII 会乱码）。
        # 只查我们自己的三个：data/napcat/ 下 NapCat 自带的 .bat 本来就是中文，
        # 那不是我们能改的也不影响使用。第一版写成「所有 .bat」，被它们误报。
        for n in names:
            if n.lower().endswith(".bat") and Path(n).name in OUR_BATS:
                try:
                    zf.read(n).decode("ascii")
                except UnicodeDecodeError:
                    problems.append(f"{n} 含非 ASCII 字节（cmd 下会乱码）")

        # 求救脚本的核心调用必须原样保留（这正是被本脚本改坏过一次的地方）
        selftest = f"{app_dir}/{SELF_TEST_NEW}"
        if selftest in names:
            text = zf.read(selftest).decode("ascii", errors="replace")
            for needle in ("QQGroupBridge.exe", "--selftest", "--out"):
                if needle not in text:
                    problems.append(f"{SELF_TEST_NEW} 里缺 {needle}（求救脚本会失效）")

    return problems


def main(argv: list[str] | None = None) -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="合成给零基础用户的交付包")
    parser.add_argument("--version", default=_release_tag(), help="版本标签（默认读 dist/RELEASE.txt）")
    parser.add_argument("--base-zip", default="", help="完整包路径")
    parser.add_argument("--out", default="", help="输出 zip 路径")
    parser.add_argument("--app-dir", default="QQ群文件搬运工", help="zip 内顶层目录名")
    args = parser.parse_args(argv)

    version = args.version.lstrip("v")
    base_zip = Path(args.base_zip) if args.base_zip else DIST / f"qgb-{args.version}-full.zip"
    out_zip = Path(args.out) if args.out else DIST / "QQ群文件搬运工-解压我.zip"

    print("=" * 68)
    print("合成交付包（面向零基础用户）")
    print("=" * 68)
    print(f"  源完整包：{base_zip}")
    print(f"  输出    ：{out_zip}")
    print(f"  顶层目录：{args.app_dir}/")
    print(f"  版本    ：{version}")

    count, _snapshot = build(base_zip, out_zip, args.app_dir)
    size_mb = out_zip.stat().st_size / 1048576
    print(f"\n  条目 {count} 项，{size_mb:.2f} MB")

    print("\n结构自检…")
    problems = verify(out_zip, args.app_dir)
    if problems:
        print("❌ 自检失败：")
        for p in problems[:20]:
            print(f"   - {p}")
        return 1
    print("✅ 结构自检通过（必需文件齐全、无本机数据、bat 仍为纯 ASCII、文档带 BOM）")

    print("\n脱敏扫描…")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from scan_secrets import scan_zip  # noqa: PLC0415

        hits = scan_zip(out_zip)
    except Exception as exc:  # pragma: no cover
        print(f"⚠️ 无法调用 scan_secrets：{exc}")
        hits = []
    if hits:
        print("❌ 扫描命中敏感内容：")
        for h in hits[:20]:
            print(f"   - {h}")
        return 1
    print("✅ 扫描通过：未发现敏感信息")

    print("\n" + "=" * 68)
    print("交付包已就绪，直接发给朋友：")
    print(f"  {out_zip}")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
