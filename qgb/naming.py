"""网盘目录命名 —— 决定每个群的文件放进哪个子目录。

规则（优先级从高到低）：

1. **用户为某个群指定的目录名**（``group_folder_names``）
2. 按 ``upload.folder_style``：

   ============  ==========================================
   ``id``        群号，例如 ``123456789``（默认，保持原行为）
   ``name``      群名，例如 ``示例学习群``
   ``id_name``   群号_群名，例如 ``123456789_示例学习群``
   ============  ==========================================

**为什么必须做名字净化**：群名是用户随便起的，可能含 ``/ \\ : * ? " < > |``
等路径非法字符，甚至含 ``..``。直接拿来做目录名会造出意外层级，
或被 WebDAV / Windows 拒绝。这里统一清洗，并在清洗后为空时退回群号 ——
**宁可名字难看，也不能让搬运失败**。
"""

from __future__ import annotations

import re

__all__ = [
    "FOLDER_STYLES",
    "sanitize_folder",
    "resolve_group_folder",
    "parse_folder_map",
    "format_folder_map",
]

#: 可选的命名风格 → 界面显示名
FOLDER_STYLES: dict[str, str] = {
    "id": "群号（123456789）",
    "name": "群名（示例学习群）",
    "id_name": "群号_群名（123456789_示例学习群）",
}

#: Windows 与 WebDAV 都不接受的字符（外加控制字符）
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Windows 保留的设备名（大小写不敏感），做目录名会被拒
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

#: 目录名长度上限（Windows 单段 255，留足余量并考虑中文按字节计）
MAX_FOLDER_LEN = 80


def sanitize_folder(name: str, fallback: str = "") -> str:
    """把任意字符串清洗成安全的单层目录名。

    处理内容：
      * 去掉 Windows/WebDAV 非法字符与控制字符
      * 掐掉首尾的空白与点号（``.`` / ``..`` 这类会造成路径穿越或隐藏目录）
      * 避开 Windows 保留设备名（``CON`` / ``NUL`` / ``COM1`` …）
      * 限制长度，并去掉因此产生的尾随点号
      * 清洗后为空时退回 ``fallback``
    """
    text = str(name or "").strip()

    # 全角字符常被误当成合法分隔符，先归一化几个最常见的
    text = text.replace("\u3000", " ").replace("／", "_").replace("＼", "_")

    text = _ILLEGAL.sub("_", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" .")          # 首尾的点与空格：Windows 会静默丢弃，易生歧义

    if len(text) > MAX_FOLDER_LEN:
        text = text[:MAX_FOLDER_LEN].rstrip(" .")

    # 清洗后如果只剩替换出来的下划线/标点（例如群名是 "///" → "___"），
    # 那就是个毫无意义的目录名，而且多个这样的群会撞进同一个目录。
    # 这种情况按"空"处理，退回群号。
    # 注意用 [^\W_]（Unicode 语义）：中文属于 \w，不会被误判。
    if text and not re.search(r"[^\W_]", text):
        text = ""

    if text and text.lower().split(".")[0] in _RESERVED:
        text = f"_{text}"

    return text or str(fallback or "")


def resolve_group_folder(
    group_id: str,
    *,
    custom: str = "",
    style: str = "id",
    group_name: str = "",
) -> str:
    """决定某个群在网盘里的子目录名。

    ``custom`` 是用户为该群显式指定的名字，优先级最高；
    其次按 ``style`` 组合群号与群名；群名缺失时一律退回群号，
    保证**任何情况下都有可用的目录名**。
    """
    gid = str(group_id or "").strip()
    if not gid:
        return ""

    if custom:
        return sanitize_folder(custom, fallback=gid)

    style = (style or "id").strip().lower()
    name = sanitize_folder(group_name, fallback="")

    if style == "name" and name:
        return name
    if style == "id_name" and name:
        return sanitize_folder(f"{gid}_{name}", fallback=gid)
    return sanitize_folder(gid, fallback=gid)


def parse_folder_map(text: str) -> dict[str, str]:
    """解析界面里的「群号 = 目录名」多行文本。

    容错规则（面向手工输入）：

    * 支持 ``=`` ``：`` ``:`` 作分隔符，也允许只用空白分隔
    * 忽略空行与 ``#`` 开头的注释行
    * 群号必须是纯数字，否则忽略该行（避免把写错的说明文字当成配置）
    * 同名目录冲突时保留先出现的，并**不做静默改名** —— 由调用方决定
      怎么提示（这里只负责解析，保持确定性）
    """
    result: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # 先按 = / ：/ : 切分
        parts = re.split(r"[=:：]", line, maxsplit=1)
        if len(parts) == 2:
            gid, name = parts[0].strip(), parts[1].strip()
        else:
            # 退而求其次：空白分隔
            bits = line.split(None, 1)
            if len(bits) != 2:
                continue
            gid, name = bits[0].strip(), bits[1].strip()

        if not gid.isdigit():
            continue
        result[gid] = name
    return result


def format_folder_map(mapping: dict[str, str]) -> str:
    """把映射还原成界面文本（``群号 = 目录名`` 每行一条）。"""
    return "\n".join(f"{k} = {v}" for k, v in sorted(mapping.items()) if v)


def has_folder_conflicts(mapping: dict[str, str], group_ids: list[str]) -> list[str]:
    """找出会让两个群写进同一个目录的配置。

    这不是致命错误（可以故意合并），但值得提醒：用户多半是笔误。
    """
    seen: dict[str, list[str]] = {}
    for gid in group_ids:
        folder = resolve_group_folder(
            gid, custom=mapping.get(gid, ""), style="id"
        )
        seen.setdefault(folder, []).append(gid)
    return [f"{folder} ← 群 {', '.join(gids)}" for folder, gids in seen.items() if len(gids) > 1]
