"""QQ群文件搬运工 —— 核心包。

设计原则（见 README「安全设计」）：
  * 分发包零凭据
  * 凭据仅以 DPAPI 加密形式落盘
  * 日志一律脱敏
  * 不向任何第三方上报数据
"""

from .version import __version__, APP_NAME, APP_ID

__all__ = ["__version__", "APP_NAME", "APP_ID"]
