"""日志初始化：滚动文件 + 控制台，**全部经过脱敏过滤器**。

默认只保留最近若干天日志，且日志中不会出现任何明文凭据。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any

from .redact import redact_text

__all__ = ["setup_logging", "RedactingFilter", "get_logger"]

_CONFIGURED = False


class RedactingFilter(logging.Filter):
    """在日志落地前把 msg / args / 异常文本全部脱敏。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)

            args = record.args
            if isinstance(args, dict):
                record.args = {k: redact_text(str(v)) for k, v in args.items()}
            elif isinstance(args, tuple):
                record.args = tuple(redact_text(str(a)) for a in args)
            elif args is not None:
                record.args = (redact_text(str(args)),)

            if record.exc_text:
                record.exc_text = redact_text(record.exc_text)
        except Exception:  # 脱敏绝不能反过来把日志搞崩
            pass
        return True


def get_logger(name: str = "qgb") -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(
    log_dir: Path | str,
    *,
    level: int | str = logging.INFO,
    console: bool = True,
    keep_days: int = 14,
    filename: str = "qgb.log",
) -> logging.Logger:
    """初始化根日志器。重复调用是安全的（幂等）。"""
    global _CONFIGURED

    logger = logging.getLogger("qgb")
    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    logger.handlers.clear()

    redactor = RedactingFilter()
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / filename,
        when="midnight",
        backupCount=max(1, keep_days),
        encoding="utf-8",
        delay=True,
    )
    file_handler.setFormatter(fmt)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        stream.addFilter(redactor)
        logger.addHandler(stream)

    # 兜底：把第三方库（requests/urllib3）的日志也纳入根日志器
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True

    _CONFIGURED = True
    return logger


def reset_logging_for_tests() -> None:
    """仅供测试：允许重新配置。"""
    global _CONFIGURED
    _CONFIGURED = False
    logging.getLogger("qgb").handlers.clear()


def log_exception(logger: logging.Logger, exc: BaseException, context: str = "") -> dict[str, Any]:
    """把异常转成「面向部署方」的结构，供 GUI 弹窗使用。"""
    from .errors import QgbError

    if isinstance(exc, QgbError):
        title, hint = exc.title, exc.hint
    else:
        title, hint = "发生未预期的错误", "请把日志文件提供给开发者。"

    detail = redact_text(f"{type(exc).__name__}: {exc}")
    logger.error("%s%s", f"{context} - " if context else "", detail)
    return {"title": title, "hint": hint, "detail": detail, "context": context}
