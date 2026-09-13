"""统一异常类型。

所有可预期的失败都从这里派生，便于 GUI 层做「可操作的提示」
（而不是把 traceback 直接甩给部署方）。
"""

from __future__ import annotations


class QgbError(Exception):
    """本项目所有可预期异常的基类。"""

    #: 面向部署方的简短标题
    title: str = "出错了"

    #: 面向部署方的可操作建议（GUI 弹窗正文）
    hint: str = ""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint


class ConfigError(QgbError):
    title = "配置有误"
    hint = "请打开「设置」检查群号与过滤规则。"


class CredentialError(QgbError):
    title = "凭据不可用"
    hint = "请重新完成授权。凭据与本机当前 Windows 用户绑定，更换电脑或用户后需要重新授权。"


class PlatformUnsupported(QgbError):
    title = "系统不支持"
    hint = "凭据加密依赖 Windows DPAPI，仅支持 Windows 10/11。"


class CryptoError(QgbError):
    """加解密失败（DPAPI 调用异常）。"""

    title = "凭据加密失败"
    hint = (
        "凭据与本机当前 Windows 用户绑定，更换电脑或用户后需要重新授权；"
        "若凭据文件损坏，请先清除凭据再重新授权。"
    )


class NapCatError(QgbError):
    title = "QQ 组件异常"
    hint = "请检查 NapCat 是否已安装并启动。"


class LoginRequired(QgbError):
    title = "需要登录 QQ"
    hint = "请使用手机 QQ 扫描二维码重新登录。"


class TransientError(QgbError):
    """**临时性**故障：值得稍后重试，且**不该消耗重试次数**。

    为什么需要单独一类：有些失败并不是"这个文件有问题"，而是组件的
    会话状态暂时对不上。典型例子（真机实测）::

        get_group_file_url: real fileUUID not found!

    NapCat 取直链依赖它内部的 fileUUID 映射，而该映射**只对当前会话里
    枚举过的文件有效**；映射失效时所有文件都取不到直链，但稍后（重新枚举、
    组件恢复后）又能取到。若把它当成普通失败，重试几次就把文件永久标成
    「失败」——用户看到的就是"任务没有推进、一批文件全卡住"。

    处理方式：退回待处理队列、**不 +1 重试次数**、稍后自然重试。
    """

    hint = "这是临时状态，稍后会自动重试；若一直失败，请重启 QQ 组件再试。"


class DownloadError(QgbError):
    title = "下载失败"
    hint = "将自动重试；若持续失败，请检查网络与群文件是否已被删除。"


class UploadError(QgbError):
    title = "上传失败"
    hint = "请检查网盘授权是否过期、OpenList 是否在运行、磁盘空间是否充足。"
