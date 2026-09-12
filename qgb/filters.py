"""文件名 / 体积筛选规则引擎。

优先级：**排除 > 扩展名白名单 > 体积区间 > 包含**

刻意做成「先判否、后判是」，并在返回里带上原因，便于 GUI 显示
「为什么这个文件被跳过了」——部署方最需要的就是这个可解释性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import FilterConfig
from .errors import ConfigError

__all__ = ["CompiledFilters", "FilterDecision", "compile_filters"]


@dataclass(slots=True)
class FilterDecision:
    accepted: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.accepted


class CompiledFilters:
    """把 :class:`FilterConfig` 预编译成正则，避免每个文件重复编译。"""

    def __init__(self, cfg: FilterConfig) -> None:
        self.cfg = cfg
        try:
            self._include = [re.compile(p, re.IGNORECASE) for p in cfg.include or []]
            self._exclude = [re.compile(p, re.IGNORECASE) for p in cfg.exclude or []]
        except re.error as exc:
            raise ConfigError(f"过滤正则无效：{exc}") from exc

        self._exts = {e.lower().lstrip(".") for e in (cfg.extensions or []) if e}
        self._min = int(cfg.min_size_mb * 1024 * 1024) if cfg.min_size_mb else 0
        self._max = int(cfg.max_size_mb * 1024 * 1024) if cfg.max_size_mb else 0

    # -------------------------------------------------- 工具

    @staticmethod
    def extension_of(name: str) -> str:
        """取扩展名（小写、不含点）。``a.tar.gz`` → ``gz``。"""
        base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        if "." not in base:
            return ""
        return base.rsplit(".", 1)[-1].lower()

    # -------------------------------------------------- 判定

    def check(self, name: str, size: int = 0) -> FilterDecision:
        name = name or ""

        for pattern in self._exclude:
            if pattern.search(name):
                return FilterDecision(False, f"命中排除规则 /{pattern.pattern}/")

        if self._exts:
            ext = self.extension_of(name)
            if ext not in self._exts:
                allowed = ", ".join(sorted(self._exts))
                return FilterDecision(False, f"扩展名 .{ext} 不在白名单（{allowed}）")

        if self._min and size < self._min:
            return FilterDecision(False, f"体积 {size / 1024 / 1024:.2f}MB 小于下限")
        if self._max and size > self._max:
            return FilterDecision(False, f"体积 {size / 1024 / 1024:.2f}MB 超过上限")

        if self._include:
            for pattern in self._include:
                if pattern.search(name):
                    return FilterDecision(True, f"命中包含规则 /{pattern.pattern}/")
            return FilterDecision(False, "未命中任何包含规则")

        # include 为空视为「全部通过」
        return FilterDecision(True, "未设置包含规则，默认通过")

    # -------------------------------------------------- 自检

    def selftest(self, samples: list[tuple[str, int]] | None = None) -> list[dict]:
        """给 GUI 的「规则试跑」按钮：用样例文件看命中结果。"""
        samples = samples or [
            ("数据_2026Q1.xlsx", 2 * 1024 * 1024),
            ("报告_v3.pdf", 800 * 1024),
            ("会议记录.docx", 120 * 1024),
            ("~$临时.xlsx", 1024),
            ("打包.zip", 50 * 1024 * 1024),
            ("readme.txt", 2048),
        ]
        out = []
        for name, size in samples:
            decision = self.check(name, size)
            out.append(
                {
                    "name": name,
                    "size_mb": round(size / 1024 / 1024, 2),
                    "accepted": decision.accepted,
                    "reason": decision.reason,
                }
            )
        return out


def compile_filters(cfg: FilterConfig) -> CompiledFilters:
    return CompiledFilters(cfg)
