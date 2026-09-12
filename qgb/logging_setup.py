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
    _close_handlers(logger)

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
        _close_handlers(logging.getLogger(noisy))
        logging.getLogger(noisy).propagate = True

    _CONFIGURED = True
    return logger


def _close_handlers(logger: logging.Logger) -> None:
    """移除并**关闭**日志器上的全部 handler。

    ⚠️ 不能只调 ``handlers.clear()``：那样 FileHandler 持有的文件对象一直不关，
    ``TimedRotatingFileHandler``（``delay=True`` 时也会在首次写入后打开）留下的
    句柄要等 GC 才释放，于是解释器刷出成片的
    ``ResourceWarning: unclosed file ...\\logs\\qgb.log``。

    真实影响：CI 日志被这些警告淹没（一次运行 30+ 条），
    真正的失败信息反而被埋掉 —— 排查时吃过这个亏。
    ``-W error::ResourceWarning`` 时更会直接失败。
    """
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - 关闭失败不该影响调用方
            pass


def reset_logging_for_tests() -> None:
    """仅供测试：允许重新配置，并释放上一次的日志文件句柄。"""
    global _CONFIGURED
    _CONFIGURED = False
    _close_handlers(logging.getLogger("qgb"))


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
