"""日志与展示用的脱敏工具。

**硬约束**：任何进入日志、UI 或诊断输出的文本，都必须先过这里。

覆盖三类敏感信息：
  1. 凭据类 —— token / cookie / BDUSS / 密码 / 签名参数
  2. 身份类 —— 手机号、QQ 号、群号等可定位到自然人的 ID
  3. 会员类 —— 会员状态、容量配额等（在本模块中不mask，而是**不采集**）
"""

from __future__ import annotations

import re

__all__ = [
    "mask_secret",
    "mask_id",
    "redact_text",
    "register_secret",
    "clear_registered",
]

# ---------------------------------------------------------------- 值掩码

#: 运行时登记的真实凭据值，命中即整体替换（最高优先级）
_REGISTERED: set[str] = set()

#: 短于该长度的登记值不参与替换，避免误伤普通文本
_MIN_REGISTERED_LEN = 6


def register_secret(value: str | None) -> None:
    """登记一个真实凭据值，后续所有日志中出现它都会被替换。

    由 :class:`qgb.secrets.SecretStore` 在解密后调用，
    这样即使代码里有遗漏，日志里也不会出现明文凭据。
    """
    if value and len(value) >= _MIN_REGISTERED_LEN:
        _REGISTERED.add(value)


def clear_registered() -> None:
    """清空登记表（进程退出或凭据轮换时调用）。"""
    _REGISTERED.clear()


def mask_secret(value: str | None, *, keep: int = 4) -> str:
    """把凭据压成 ``abcd…wxyz`` 形式；过短则整体打码。"""
    if not value:
        return "(空)"
    s = str(value)
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}…{s[-keep:]}"


def mask_id(value: str | int | None, *, keep_head: int = 3, keep_tail: int = 3) -> str:
    """遮蔽账号/群号等标识：``123456789`` → ``123***789``。"""
    if value is None:
        return "(空)"
    s = str(value)
    if len(s) <= keep_head + keep_tail:
        return "*" * len(s)
    return f"{s[:keep_head]}{'*' * (len(s) - keep_head - keep_tail)}{s[-keep_tail:]}"


# ---------------------------------------------------------------- 文本规则

#: (正则, 替换) —— 顺序敏感，先长后短
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ⚠️ 必须排在最前：``Authorization: Bearer eyJ...`` 里的令牌是**空格分隔**的，
    # 若先跑下面的字段规则，只会吃掉 "Bearer" 而把真正的令牌留在日志里。
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9\-._~+/=]{8,}"), r"\1 ***"),
    # JSON / query 中的凭据字段
    (
        re.compile(
            r'(?i)("?(?:refresh_token|access_token|id_token|session_secret|'
            r"client_secret|app_?secret|api_?key|apikey|password|passwd|pwd|"
            r"bduss|stoken|ptoken|skey|bds_token|authorization|auth|token)"
            r'"?\s*[:=]\s*)("?)([^",\s&;}\']+)(\2)'
        ),
        r"\1\2***\4",
    ),
    # URL 中的签名/令牌参数
    (
        re.compile(r"(?i)([?&](?:sign|signature|access_token|token|auth|key)=)[^&\s]+"),
        r"\1***",
    ),
    # 手机号
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), lambda m: mask_id(m.group(0))),
    # 邮件
    (
        re.compile(r"([A-Za-z0-9._%+\-]{1,64})@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})"),
        lambda m: f"{m.group(1)[:2]}***@{m.group(2)}",
    ),
)


def redact_text(text: str) -> str:
    """对任意文本做脱敏，可安全写入日志。"""
    if not text:
        return text
    out = str(text)

    # 1) 已登记的真实凭据优先整体替换
    for secret in _REGISTERED:
        if secret and secret in out:
            out = out.replace(secret, "***")

    # 2) 通用规则
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)

    return out
