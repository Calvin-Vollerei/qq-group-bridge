"""非敏感配置（``config.json``）。

**这里的任何字段都不允许存放凭据。** 凭据一律走 :mod:`qgb.secrets`。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .secrets import default_data_dir

__all__ = [
    "AppConfig",
    "FilterConfig",
    "NapCatConfig",
    "UploadConfig",
    "MonitorConfig",
    "default_config_path",
    "default_temp_dir",
    "load_config",
    "save_config",
    "RE_FILTER_TEMPLATES",
]

#: 常用正则模板（GUI 的下拉候选）
RE_FILTER_TEMPLATES: list[tuple[str, str]] = [
    ("全部文件", r".*"),
    ("仅 Excel/PDF", r".*\.(xlsx|xls|xlsm|pdf)$"),
    ("仅数据文件", r"数据_.*\.(xlsx|xls|csv)$"),
    ("仅压缩包", r".*\.(zip|rar|7z|tar\.gz)$"),
    ("仅文档", r".*\.(docx?|pptx?|pdf|xlsx?)$"),
    ("排除临时文件", r"^(?!.*(~\$|\.tmp$|\.crdownload$)).*$"),
    ("只要今天之后的命名", r".*20\d{2}[-_]?\d{2}[-_]?\d{2}.*"),
]


def default_data_dir_str() -> str:
    return str(default_data_dir())


def default_temp_dir() -> Path:
    return default_data_dir() / "tmp"


def default_config_path() -> Path:
    return default_data_dir() / "config.json"


# ------------------------------------------------------------------ 子配置

@dataclass
class FilterConfig:
    """文件名/大小筛选规则。"""

    #: 正则包含列表——命中任意一条即视为候选
    include: list[str] = field(default_factory=lambda: [r".*"])
    #: 正则排除列表——命中任意一条即丢弃（优先级高于 include）
    exclude: list[str] = field(default_factory=list)
    #: 扩展名白名单（小写、不含点）；为空表示不限制
    extensions: list[str] = field(default_factory=list)
    #: 最小体积（MB），0 表示不限
    min_size_mb: float = 0.0
    #: 最大体积（MB），0 表示不限
    max_size_mb: float = 0.0


@dataclass
class NapCatConfig:
    """NapCat / OneBot 接入参数。"""

    #: NapCat 安装目录；为空则用应用数据目录下的 napcat/
    install_dir: str = ""
    #: OneBot HTTP API 地址（NapCat 的 HTTP 服务端）
    api_base: str = "http://127.0.0.1:3000"
    #: NapCat WebUI 地址（用于取登录二维码 / 健康检查）
    webui_base: str = "http://127.0.0.1:6099"
    #: 启动主程序时是否自动拉起 NapCat
    autostart: bool = True
    #: 等待 NapCat 就绪的最长秒数
    start_timeout_sec: int = 90
    #: 首次运行是否允许自动下载安装 NapCat（不随分发包捆绑）
    allow_auto_install: bool = True
    #: 已安装 QQ NT 的入口路径（挂钩模式使用）。
    #:
    #: **留空表示自动从注册表探测**，通常不需要填。
    #: 只有当 QQ 装在非常规位置、注册表也查不到时才需要手动指定，
    #: 例如 ``D:\QQ\QQ.exe``。
    qq_path: str = ""
    #: 以**单进程模式**运行 NapCat（设置 NAPCAT_DISABLE_MULTIPROCESSING=1）。
    #:
    #: 默认开启，理由：NapCat 默认会 fork 一个 worker 子进程，父子进程之间走
    #: Windows 命名管道 IPC，并给 worker 传 Electron 专有的 --no-sandbox 参数。
    #: 在受限环境（沙箱、部分安全软件、精简系统）下这两点都会让启动失败，
    #: 表现为 ``spawn EPERM`` 或 ``node: bad option: --no-sandbox``。
    #: 单进程模式没有这些依赖；对一个群文件搬运工具而言开销完全可接受。
    single_process: bool = True


@dataclass
class UploadConfig:
    """上传目标（推荐 OpenList WebDAV 中转）。"""

    #: webdav | local
    adapter: str = "webdav"
    #: 远端根目录
    remote_root: str = "/QQ群备份"
    #: OpenList 的 WebDAV 入口
    webdav_url: str = "http://127.0.0.1:5244/dav"
    #: local 适配器的目标目录
    local_root: str = ""
    #: OpenList 所在目录。留空则自动探测（程序目录旁、同级、data\openlist）
    openlist_dir: str = ""
    #: 是否随程序自动启停本机 OpenList。
    #:
    #: OpenList 是独立进程、不会自己启动；忘了开就只会在"测试连接"里显示
    #: 一句连接失败，很难联想到根因。默认开启，**且只在 WebDAV 指向本机时
    #: 生效** —— 将来把 OpenList 放到云服务器上，这里会自动跳过。
    manage_openlist: bool = True
    #: 上传后是否回读校验（HEAD/PROPFIND 比对大小）
    verify_after_upload: bool = True
    #: 远端是否按群号分子目录
    split_by_group: bool = True
    #: 群目录的命名风格：``id`` / ``name`` / ``id_name``（见 qgb/naming.py）。
    #:
    #: 默认 ``id``（群号）—— 与历史行为一致，且群号唯一、绝无冲突。
    #: 想要在网盘里一眼认出是哪个群，就改成 ``id_name`` 或 ``name``；
    #: 也可以在每个群的「自定义目录名」里逐个指定（优先级更高）。
    folder_style: str = "id"


@dataclass
class MonitorConfig:
    """监控与搬运行为。"""

    #: 轮询间隔（秒）。过短会显著提高 QQ 风控概率
    poll_interval_sec: int = 300
    #: 轮询抖动（秒），避免固定节律
    jitter_sec: int = 30
    #: 同时下载数。建议保持 1，避免风控
    download_concurrency: int = 1
    #: 单个文件的最大重试次数
    max_retries: int = 3
    #: 重试退避基数（秒），按 attempts 递增
    retry_backoff_sec: int = 30
    #: 本地保留天数；0 = 上传成功后立即删除
    keep_local_days: int = 0
    #: 单文件体积上限（MB），0 表示不限
    max_file_mb: float = 0.0
    #: 低于该可用磁盘（GB）时暂停搬运
    min_free_disk_gb: float = 2.0
    #: 是否抓取群文件夹（子目录）内的文件
    recursive_folders: bool = True
    #: 每个群单次拉取的文件条数
    page_size: int = 50
    #: 下载"卡死"判定（秒）：连续这么久没有收到任何数据就放弃该文件、继续下一个。
    #:
    #: 为什么需要它：requests 的 read timeout 只在"读操作超时"时触发，
    #: 服务器接受了连接却不再发数据时可能长时间不返回；而本工具单线程逐文件处理，
    #: 一个卡住的下载会让整批看起来"卡死"。20 秒无数据即判定卡住，
    #: 失败会被记录并稍后自动重试（不会丢掉文件）。
    stall_timeout_sec: float = 20.0
    #: 临时性故障（如 NapCat 的 fileUUID 未就绪）后的**冷却秒数**。
    #:
    #: 为什么要冷却：这类失败不消耗重试次数，若不冷却就会**下一轮立刻又排队首**，
    #: 把新一轮的尝试名额全部吃掉，真正的新文件一直轮不到 —— 用户看到的是
    #: "下载始终不开始"。冷却期内它不进待处理队列，其它文件照常推进。
    transient_cooldown_sec: float = 300.0


@dataclass
class AppConfig:
    """顶层配置。"""

    #: 要监控的群号列表（字符串，避免前导零/精度问题）
    groups: list[str] = field(default_factory=list)
    #: 每个群在网盘里的**子目录名**（可选）：``{"123456789": "示例学习群"}``。
    #:
    #: 留空则按 ``upload.folder_style`` 自动命名。默认风格是群号，
    #: 而群号在网盘里很难辨认，所以想看得懂就填这里。
    group_folder_names: dict[str, str] = field(default_factory=dict)
    filters: FilterConfig = field(default_factory=FilterConfig)
    napcat: NapCatConfig = field(default_factory=NapCatConfig)
    upload: UploadConfig = field(default_factory=UploadConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    #: 临时下载目录；为空则用应用数据目录下的 tmp/
    temp_dir: str = ""
    #: 日志保留天数
    log_keep_days: int = 14
    #: 配置结构版本，便于日后迁移
    schema_version: int = 1

    # -------------------------------------------------- 校验

    def validate(self) -> list[str]:
        """返回问题列表（空列表 = 通过）。"""
        problems: list[str] = []

        if not self.groups:
            problems.append("尚未添加任何 QQ 群号。")
        seen: set[str] = set()
        for g in self.groups:
            if not g.isdigit() or not (5 <= len(g) <= 12):
                problems.append(f"群号格式不正确：{g}")
            elif g in seen:
                problems.append(f"群号重复：{g}")
            seen.add(g)

        import re

        for label, patterns in (("包含", self.filters.include), ("排除", self.filters.exclude)):
            for pattern in patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    problems.append(f"{label}正则无效（{pattern}）：{exc}")

        if self.filters.min_size_mb and self.filters.max_size_mb:
            if self.filters.min_size_mb > self.filters.max_size_mb:
                problems.append("最小体积大于最大体积。")

        if self.monitor.poll_interval_sec < 30:
            problems.append("轮询间隔过短（建议 ≥ 60 秒），会显著提高 QQ 风控概率。")
        if self.monitor.download_concurrency < 1:
            problems.append("并发下载数必须 ≥ 1。")

        if self.upload.adapter not in ("webdav", "local"):
            problems.append(f"未知的上传适配器：{self.upload.adapter}")
        if self.upload.adapter == "webdav" and not self.upload.webdav_url:
            problems.append("WebDAV 地址不能为空。")
        if self.upload.adapter == "local" and not self.upload.local_root:
            problems.append("本地目标目录不能为空。")

        return problems

    def resolved_temp_dir(self) -> Path:
        return Path(self.temp_dir) if self.temp_dir else default_temp_dir()

    # -------------------------------------------------- 序列化

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        if not isinstance(data, dict):
            raise ConfigError("配置文件内容不是对象。")

        def build(dc_type, raw):
            if not isinstance(raw, dict):
                return dc_type()
            allowed = {f.name for f in fields(dc_type)}
            known = {k: v for k, v in raw.items() if k in allowed}
            try:
                return dc_type(**known)
            except TypeError as exc:
                raise ConfigError(f"配置字段类型不正确：{exc}") from exc

        groups_raw = data.get("groups") or []
        if not isinstance(groups_raw, list):
            raise ConfigError("groups 必须是列表。")
        groups = [str(g).strip() for g in groups_raw if str(g).strip()]

        cfg = cls(
            groups=groups,
            filters=build(FilterConfig, data.get("filters")),
            napcat=build(NapCatConfig, data.get("napcat")),
            upload=build(UploadConfig, data.get("upload")),
            monitor=build(MonitorConfig, data.get("monitor")),
            temp_dir=str(data.get("temp_dir") or ""),
            log_keep_days=int(data.get("log_keep_days") or 14),
            schema_version=int(data.get("schema_version") or 1),
        )

        # 顺手做一次轻量纠偏
        cfg.filters.extensions = [
            str(e).lower().lstrip(".") for e in (cfg.filters.extensions or [])
        ]
        return cfg


# ------------------------------------------------------------------ 读写

def load_config(path: Path | str | None = None) -> AppConfig:
    """读取配置；文件不存在时返回默认配置（不写盘）。"""
    p = Path(path) if path is not None else default_config_path()
    if not p.exists():
        return AppConfig()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"配置文件无法解析：{exc}") from exc
    return AppConfig.from_dict(raw)


def save_config(cfg: AppConfig, path: Path | str | None = None) -> Path:
    """原子写入配置（**不含任何凭据**）。"""
    p = Path(path) if path is not None else default_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, p)
    return p


def app_paths() -> dict[str, str]:
    """集中暴露运行期路径，便于 GUI 的「打开目录」按钮。"""
    base = default_data_dir()
    return {
        "data_dir": str(base),
        "config": str(base / "config.json"),
        "secrets": str(base / "secrets.enc"),
        "logs": str(base / "logs"),
        "temp": str(base / "tmp"),
        "state_db": str(base / "state.db"),
    }
