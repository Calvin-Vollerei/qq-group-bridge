"""给 405 加熔断：连续多次被拒就停下来提示，而不是无限重试。

背景（真实事故）：OpenList 的上传配置被写坏（`upload_api` 变空 → 上传主机
解析失败），OpenList 对**所有** PUT 都返回 405。而程序把 405 当成"网盘暂时
拒绝接收 → 稍后自动重试"，于是：
  · 每轮都重下几百 MB 的大文件（白白耗流量和时间）；
  · 界面上一片"暂时跳过"，用户以为只是网络抖动，找不到真正原因。

405 确实**多数时候**是偶发（同一目录里 201 与 405 混合出现，文件名特征无差别），
所以不能一遇 405 就判死。折中做法是**熔断**：
  · 连续 N 次 405 → 判定为"服务端持续拒绝"，停止重试并明确提示排查方向；
  · 任何一次成功即清零计数（正常抖动不会触发熔断）。

这样偶发抖动照旧自动重试，而永久性故障会**快速暴露**、不再空转。
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

#: 连续多少次 405 判定为"服务端持续拒绝"（熔断阈值）
TRIP_AFTER = 5

#: 连续 405 计数（进程内）
_consecutive_405 = 0


def note_405() -> None:
    """记录一次 405。"""
    global _consecutive_405
    _consecutive_405 += 1
    log.debug("连续 405 计数：%d", _consecutive_405)


def note_success() -> None:
    """上传成功：清零计数（说明服务端是好的）。"""
    global _consecutive_405
    if _consecutive_405:
        log.debug("上传成功，连续 405 计数清零（原为 %d）", _consecutive_405)
    _consecutive_405 = 0


def is_tripped() -> bool:
    """是否已熔断（连续 405 达到阈值）。"""
    return _consecutive_405 >= TRIP_AFTER


def count() -> int:
    return _consecutive_405


def trip_hint() -> str:
    """熔断后给用户的排查提示（写到日志与界面上）。"""
    return (
        f"连续 {_consecutive_405} 次上传都被服务端拒绝（HTTP 405）——"
        "这不是偶发抖动，而是 OpenList 侧持续拒绝写入。请按顺序检查：\n"
        "  1) OpenList 是否在运行（浏览器打开 http://127.0.0.1:5244 应能看到界面）；\n"
        "  2) 百度网盘的授权是否还有效（打开 OpenList 日志 "
        "dist\\OpenList\\data\\log\\log.log，搜 errno：\n"
        "        errno 10 / 31353 = 授权或令牌问题，需要重新授权百度网盘）；\n"
        "  3) OpenList 存储配置里的上传地址是否为空（upload_api 应为 "
        "https://d.pcs.baidu.com）；\n"
        "  4) 网盘空间是否已满。\n"
        "处理完在「监控」页点「重试失败项」即可继续；本程序已暂停重试，"
        "不会再重复下载大文件。"
    )


def reset() -> None:
    """人工重置（例如用户点了「重试失败项」）。"""
    global _consecutive_405
    _consecutive_405 = 0
