"""分析 405 失败的文件名特征 —— 找出百度驱动拒绝的规律。

从 OpenList 日志里取所有 405 的 PUT 路径，与 201 成功的路径对比，
按字符类别统计差异（长度、空格、特殊符号、非 ASCII、全角符号等）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from _console import use_utf8_console  # noqa: E402

LOG = Path("dist/OpenList/data/log/log.log")


def extract() -> tuple[list[str], list[str]]:
    bad: list[str] = []
    good: list[str] = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.search(r'\| (2\d\d|405) \|.*PUT\s+"([^"]+)"', line)
        if not m:
            continue
        code, path = m.group(1), unquote(m.group(2))
        name = path.rsplit("/", 1)[-1]
        (bad if code == "405" else good).append(name)
    return bad, good


def features(name: str) -> dict[str, object]:
    return {
        "长度": len(name),
        "含空格": " " in name,
        "含双空格": "  " in name,
        "含全角空格": "\u3000" in name,
        "含中文标点": bool(re.search(r"[，。、《》（）：；！？·—]", name)),
        "含英文括号": bool(re.search(r"[()]", name)),
        "含斜杠类": bool(re.search(r"[/\\|:*?\"<>]", name)),
        "非 ASCII 占比": round(
            sum(1 for c in name if ord(c) > 127) / max(1, len(name)), 2
        ),
    }


def summarize(label: str, names: list[str]) -> None:
    print(f"\n=== {label}（{len(names)} 个）===")
    if not names:
        print("  （无）")
        return
    keys = list(features(names[0]).keys())
    for k in keys:
        vals = [features(n)[k] for n in names]
        if isinstance(vals[0], bool):
            n = sum(1 for v in vals if v)
            print(f"  {k:12} {n}/{len(vals)} 个为真 ({n * 100 // len(vals)}%)")
        else:
            nums = [v for v in vals if isinstance(v, (int, float))]
            print(f"  {k:12} 最小 {min(nums)} / 平均 {sum(nums) / len(nums):.1f} / 最大 {max(nums)}")
    print("  样例：")
    for n in names[:5]:
        print(f"    [{len(n):3}] {n[:70]}")


def main() -> int:
    use_utf8_console()
    bad, good = extract()
    print("=" * 74)
    print("405 失败 vs 201 成功的文件名特征对比")
    print("=" * 74)
    summarize("405 失败", bad)
    summarize("201 成功", good)

    print("\n" + "=" * 74)
    print("判读：找出只在失败组里高比例出现的特征，那就是百度驱动拒绝的原因")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
