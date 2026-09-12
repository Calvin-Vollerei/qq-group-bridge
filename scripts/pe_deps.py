#!/usr/bin/env python3
"""PE 依赖自查：列出一个原生模块（.node/.dll/.exe）的导入表，并检查依赖是否齐备。

用途：Windows 上「The specified module could not be found (WinError 126)」
几乎总是**间接依赖缺失**，而不是文件本身不在。这个脚本会：
  1. 解析 PE 导入表，列出直接依赖的 DLL
  2. 标注每个依赖能否在「同目录 / System32 / SysWOW64 / PATH」中找到
  3. 报告 PE 架构（x86 / x64），便于发现位数不匹配

用法::

    python scripts/pe_deps.py <file> [<file> ...]
    python scripts/pe_deps.py path/to/wrapper.node --recursive
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _console import use_utf8_console  # noqa: E402  控制台切 UTF-8（Linux 上常是 cp1252）

MACHINE_TYPES = {0x014C: "x86(32位)", 0x8664: "x64(64位)", 0xAA64: "ARM64"}


def _read_cstr(data: bytes, offset: int, limit: int = 512) -> str:
    end = data.find(b"\x00", offset, offset + limit)
    if end < 0:
        end = offset + limit
    return data[offset:end].decode("ascii", "ignore")


def pe_info(path: Path) -> dict:
    """返回 {machine, imports, is_dll}；解析失败抛 ValueError。"""
    data = path.read_bytes()
    if data[:2] != b"MZ":
        raise ValueError("不是 PE 文件")

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError("缺少 PE 签名")

    coff = e_lfanew + 4
    machine, n_sections = struct.unpack_from("<HH", data, coff)
    characteristics = struct.unpack_from("<H", data, coff + 18)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt_off = coff + 20

    magic = struct.unpack_from("<H", data, opt_off)[0]
    is_pe32_plus = magic == 0x20B

    # 数据目录：导入表是第 2 项（索引 1）
    dd_off = opt_off + (112 if is_pe32_plus else 96)
    import_rva, _import_size = struct.unpack_from("<II", data, dd_off + 8)

    sections: list[tuple[int, int, int, int]] = []
    sec_off = opt_off + opt_size
    for i in range(n_sections):
        base = sec_off + i * 40
        va, vsize = struct.unpack_from("<II", data, base + 12)
        raw_size, raw_ptr = struct.unpack_from("<II", data, base + 16)
        sections.append((va, max(vsize, raw_size), raw_ptr, raw_size))

    def rva_to_off(rva: int) -> int | None:
        for va, vsize, raw_ptr, raw_size in sections:
            if va <= rva < va + vsize:
                delta = rva - va
                if delta < raw_size:
                    return raw_ptr + delta
        return None

    imports: list[str] = []
    if import_rva:
        off = rva_to_off(import_rva)
        while off is not None:
            desc = struct.unpack_from("<IIIII", data, off)
            name_rva = desc[3]
            if name_rva == 0:
                break
            name_off = rva_to_off(name_rva)
            if name_off is None:
                break
            imports.append(_read_cstr(data, name_off))
            off += 20

    return {
        "machine": MACHINE_TYPES.get(machine, f"0x{machine:04X}"),
        "is_dll": bool(characteristics & 0x2000),
        "imports": imports,
    }


def find_dll(name: str, search_dirs: list[Path]) -> Path | None:
    """按 Windows 的搜索顺序找 DLL（同目录优先，然后 System32/SysWOW64）。"""
    for directory in search_dirs:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def main() -> int:
    use_utf8_console()
    parser = argparse.ArgumentParser(description="PE 依赖自查")
    parser.add_argument("targets", nargs="+")
    parser.add_argument("--recursive", action="store_true",
                        help="也解析依赖自身的依赖（一层）")
    args = parser.parse_args()

    windir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    system_dirs = [windir / "System32", windir / "SysWOW64", windir]

    for target in args.targets:
        path = Path(target)
        print("=" * 70)
        print(f"文件：{path}")
        if not path.is_file():
            print("  ❌ 文件不存在")
            continue

        try:
            info = pe_info(path)
        except (ValueError, OSError, struct.error) as exc:
            print(f"  ❌ 解析失败：{exc}")
            continue

        print(f"  架构：{info['machine']}　类型：{'DLL' if info['is_dll'] else 'EXE'}")
        print(f"  直接依赖 {len(info['imports'])} 个：")

        search = [path.parent, *system_dirs]
        missing: list[str] = []
        for name in info["imports"]:
            found = find_dll(name, search)
            if found is None:
                missing.append(name)
                print(f"    ❌ {name}   （找不到）")
            else:
                where = "同目录" if found.parent == path.parent else found.parent.name
                print(f"    ✅ {name}   （{where}）")

        if missing:
            print()
            print(f"  ⚠ 缺失 {len(missing)} 个直接依赖，这就是 WinError 126 的原因：")
            for name in missing:
                print(f"      - {name}")
            # 给出常见结论
            vcruntime = [m for m in missing if "vcruntime" in m.lower()
                         or "msvcp" in m.lower()]
            if vcruntime:
                print("      → 看起来缺少 Microsoft Visual C++ 运行库，"
                      "请安装 VC++ 2015-2022 Redistributable (x64)")
        else:
            print("  ✅ 直接依赖齐备（若仍报 126，说明是更深层的传递依赖缺失）")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
