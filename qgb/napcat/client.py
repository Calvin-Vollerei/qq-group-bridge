"""OneBot 11 HTTP 客户端（面向 NapCat）。

只依赖 ``requests``，不引入机器人框架——协议层很薄，自己实现换来的是
**完全可控的重试/超时/日志脱敏**，以及可离线用假服务端测试。

对应 API（OneBot 11 / NapCat）::

    get_login_info          -> 当前登录账号状态
    get_group_list          -> 已加入的群
    get_group_file_list     -> 群文件列表（含子文件夹）
    get_group_file_url      -> 群文件下载直链

⚠️ 不同 NapCat 版本对「取直链」的动作名略有差异
（``get_group_file_url`` / ``get_group_file_download_url``），
本客户端按顺序尝试并记住可用的那个。该分支需要真机确认。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests

from ..errors import LoginRequired, NapCatError
from ..models import GroupFile

__all__ = ["OneBotClient", "LoginInfo", "OneBotHTTPError"]

log = logging.getLogger(__name__)

#: 取直链的动作名候选（按优先级）
_URL_ACTIONS = ("get_group_file_url", "get_group_file_download_url")

#: 单次拉取群文件的最大条数。
#:
#: ⚠️ **这是「只能看到最近几十个文件」的根因，必须显式传，而且要足够大。**
#:
#: ``get_group_root_files`` / ``get_group_files_by_folder`` **默认只返回约
#: 40–50 条**；传 ``file_count`` 后返回量随之上调，但**上限由服务端决定**。
#: 实测（同一台机器、不同群）：
#:
#: ==================  ==================  ==================
#: file_count          群 A 根目录返回      群 B 根目录返回
#: ==================  ==================  ==================
#: 500                 499                 422
#: 1000                999                 422
#: 1500                **1101**            422
#: 5000                1101                422
#: ==================  ==================  ==================
#:
#: 可见上限并不是固定的 1000 —— 取 1000 时**仍然会丢 102 个文件**。
#: 因此这里给一个足够大的值，由服务端自行截断（多传没有代价）。
#: 配合逐层遍历文件夹即可覆盖群文件的全部历史文件。
DEFAULT_FILE_COUNT = 5000


class OneBotHTTPError(NapCatError):
    """HTTP 层或业务 retcode 异常。"""

    def __init__(self, message: str, *, retcode: int | None = None, action: str = "") -> None:
        super().__init__(message)
        self.retcode = retcode
        self.action = action


@dataclass(slots=True)
class LoginInfo:
    """登录状态快照（**不含**昵称等画像信息，只保留脱敏所需的最小集合）。"""

    user_id: str = ""
    online: bool = False
    raw_ok: bool = False
    message: str = ""

    @property
    def category(self) -> str:
        if self.online:
            return "online"
        if self.raw_ok:
            return "offline"
        return "unknown"


class OneBotClient:
    """极简 OneBot 11 HTTP 客户端。"""

    def __init__(
        self,
        api_base: str,
        token: str | None = None,
        *,
        timeout: tuple[float, float] = (10.0, 60.0),
        file_count: int = DEFAULT_FILE_COUNT,
    ) -> None:
        self.api_base = (api_base or "").rstrip("/")
        self.token = token or ""
        self.timeout = timeout
        self.file_count = int(file_count)      # 见 DEFAULT_FILE_COUNT 的说明
        self._session = requests.Session()
        self._url_action: str | None = None  # 记住可用的取直链动作

    # -------------------------------------------------- 基础设施

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def call(self, action: str, **params: Any) -> Any:
        """调用一个 OneBot 动作，返回 ``data`` 字段。"""
        if not self.api_base:
            raise NapCatError("未配置 OneBot 地址")

        url = f"{self.api_base}/{action}"
        payload = {k: v for k, v in params.items() if v is not None}

        try:
            resp = self._session.post(
                url, json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            raise OneBotHTTPError(
                f"无法连接 QQ 组件（{exc.__class__.__name__}）",
                action=action,
            ) from exc

        if resp.status_code == 401:
            raise OneBotHTTPError("OneBot 令牌无效", retcode=401, action=action)
        if resp.status_code >= 500:
            raise OneBotHTTPError(f"QQ 组件内部错误（{resp.status_code}）", action=action)

        try:
            body = resp.json()
        except ValueError as exc:
            raise OneBotHTTPError(
                f"返回内容不是 JSON（HTTP {resp.status_code}）", action=action
            ) from exc

        if not isinstance(body, dict):
            raise OneBotHTTPError("返回结构异常", action=action)

        status = str(body.get("status") or "")
        retcode = body.get("retcode")

        if status == "failed" or (isinstance(retcode, int) and retcode not in (0, 1)):
            msg = str(body.get("message") or body.get("wording") or "未知错误")
            raise OneBotHTTPError(msg, retcode=retcode if isinstance(retcode, int) else None, action=action)

        return body.get("data")

    # -------------------------------------------------- 健康 / 登录

    def ping(self) -> bool:
        try:
            self.call("get_login_info")
            return True
        except NapCatError:
            return False

    def get_login_info(self) -> LoginInfo:
        """获取登录状态。

        ``get_login_info`` 在未登录时会失败，据此区分「未登录 / 登录失效」。
        """
        try:
            data = self.call("get_login_info")
        except OneBotHTTPError as exc:
            return LoginInfo(online=False, raw_ok=False, message=exc.message)

        if not isinstance(data, dict):
            return LoginInfo(online=False, raw_ok=False, message="返回结构异常")

        user_id = str(data.get("user_id") or "")
        return LoginInfo(
            user_id=user_id,
            online=bool(user_id) and user_id != "0",
            raw_ok=True,
            message="已登录" if user_id and user_id != "0" else "未登录",
        )

    def require_login(self) -> LoginInfo:
        info = self.get_login_info()
        if not info.online:
            raise LoginRequired(
                f"QQ 未登录或登录已失效（{info.message}）",
                hint="请使用手机 QQ 扫描二维码重新登录。",
            )
        return info

    def get_group_list(self) -> list[dict[str, Any]]:
        data = self.call("get_group_list")
        return data if isinstance(data, list) else []

    # -------------------------------------------------- 群文件

    def get_group_file_list(
        self,
        group_id: str,
        *,
        folder_id: str = "/",
        start: int = 0,
        count: int = 50,
    ) -> tuple[list[GroupFile], list[dict[str, Any]]]:
        """拉取一层群文件。返回 ``(文件列表, 子文件夹列表)``。

        ⚠️ **接口名与"不分页"都是按真机实测确定的，不是照文档猜的。**

        NapCat 里**没有** ``get_group_file_list``（调用返回
        ``不支持的Api get_group_file_list``）。实际可用的是：

        * ``get_group_root_files``       —— 根目录（参数只有 ``group_id``）
        * ``get_group_files_by_folder``  —— 指定文件夹（``group_id`` + ``folder_id``）

        **这两个接口不支持 ``start``/``count`` 分页**：服务端一次把该层全部
        返回。曾经照着"可能会分页"的假设写了翻页递归，结果因为服务端忽略
        ``start``、每轮都返回同一批而**无限递归，调用直接卡死**（热检查时
        真实踩到）。所以这里明确不分页 —— 保留这两个参数只是为了不破坏
        调用方签名，实际会被忽略。

        另外这两个接口**不需要 PacketBackend**，因此在 QQ 版本较新、
        PacketBackend 不可用的环境下依然能列文件（只是拿不到下载直链）。
        """
        _ = count          # 忽略调用方的 count：分页由服务端 file_count 决定
        is_root = (not folder_id) or folder_id in ("/", "")

        # 必须显式传 file_count，否则只能拿到最近几十条（见 DEFAULT_FILE_COUNT）
        if is_root:
            data = self.call(
                "get_group_root_files",
                group_id=int(group_id),
                file_count=self.file_count,
            )
        else:
            data = self.call(
                "get_group_files_by_folder",
                group_id=int(group_id),
                folder_id=str(folder_id),
                file_count=self.file_count,
            )

        if not isinstance(data, dict):
            return [], []

        raw_files = data.get("files") or []
        raw_folders = data.get("folders") or []

        files: list[GroupFile] = []
        seen: set[str] = set()
        for item in raw_files:
            if not isinstance(item, dict):
                continue
            gf = GroupFile.from_api(item, group_id=str(group_id), folder_id=folder_id)
            # 去重：服务端偶尔会重复返回同一条，别让它变成重复搬运
            if gf.file_id and gf.file_id not in seen:
                seen.add(gf.file_id)
                files.append(gf)

        folders = [f for f in raw_folders if isinstance(f, dict)]
        return files, folders

    def walk_group_files(
        self,
        group_id: str,
        *,
        recursive: bool = True,
        page_size: int = 50,          # 保留签名；服务端不分页，实际不使用
        max_pages_per_folder: int = 20,
        max_folders: int = 200,
    ) -> Iterator[GroupFile]:
        """深度优先遍历群文件（含子文件夹）。

        ⚠️ **不翻页。** ``get_group_root_files`` / ``get_group_files_by_folder``
        不接受 ``start``/``count``：它们一次返回该层的**全部**内容
        （上限由 ``file_count`` 决定，见 ``DEFAULT_FILE_COUNT``）。

        曾经照着"可能要翻页"的假设写了 ``for page in range(max_pages)`` 循环，
        结果因为服务端忽略 ``start``、每轮都返回同一批，**同一批文件被累加
        20 遍** —— 实测某群声明 1103 个文件却"遍历到" 19982 个（约 20 倍）。
        所以这里单次取值即可。

        仍然保留的防御：
          * 文件夹总数上限 ``max_folders``
          * 同一 ``folder_id`` 只访问一次（环检测）
          * **全局 file_id 去重**：即便服务端返回有重叠，也不会重复产出
        """
        stack: list[str] = ["/"]
        visited: set[str] = set()
        emitted: set[str] = set()
        folders_seen = 0

        while stack:
            folder = stack.pop()
            if folder in visited:
                continue
            visited.add(folder)

            files, folders = self.get_group_file_list(group_id, folder_id=folder)

            for gf in files:
                if gf.file_id in emitted:
                    continue          # 安全网：跨目录重叠也不重复产出
                emitted.add(gf.file_id)
                yield gf

            if recursive:
                for entry in folders:
                    if folders_seen >= max_folders:
                        log.warning("群 %s 文件夹数量达到上限，停止深入", group_id)
                        break
                    fid = str(
                        entry.get("folder_id")
                        or entry.get("folder")
                        or entry.get("file_id")
                        or ""
                    )
                    if fid and fid not in visited:
                        stack.append(fid)
                        folders_seen += 1

    def get_group_file_url(self, group_id: str, file_id: str, busid: int = 102) -> str:
        """获取群文件下载直链（带时效）。

        ⚠️ **错误信息必须以最有信息量的那条为准。**

        真机实测：``get_group_file_url`` 在 PacketBackend 不可用时返回的
        ``packetBackend不可用…`` 才是真正可操作的线索；而备用名
        ``get_group_file_download_url`` 根本不存在，只回一句
        ``不支持的Api``。早先的实现报的是**最后一条**错误，于是用户看到的
        是"不支持的Api get_group_file_download_url" —— 完全指向错误方向
        （会让人以为接口名写错了，实际是 QQ 版本与 NapCat 不匹配）。
        """
        candidates = [self._url_action] if self._url_action else list(_URL_ACTIONS)
        errors: list[tuple[str, str]] = []      # (动作, 错误文本)
        packet_backend_problem = ""

        for action in candidates:
            if not action:
                continue
            try:
                data = self.call(
                    action, group_id=int(group_id), file_id=str(file_id), busid=int(busid)
                )
            except OneBotHTTPError as exc:
                text = str(exc)
                errors.append((action, text))
                # 这条是"版本不匹配"的信号，优先级最高，单独记下来
                if "packetbackend" in text.lower() or "packetBackend" in text:
                    packet_backend_problem = text
                continue

            url = ""
            if isinstance(data, dict):
                url = str(data.get("url") or data.get("download_url") or "")
            elif isinstance(data, str):
                url = data

            if url:
                if self._url_action != action:
                    self._url_action = action
                    log.info("群文件直链接口确认为 %s", action)
                return url

        if packet_backend_problem:
            raise NapCatError(
                "QQ 组件无法提供下载直链：PacketBackend 不可用",
                hint=(
                    "这**不是接口名问题**，而是 NapCat 不支持你当前 QQ 的版本架构。\n"
                    "本工具已验证的解法：改用**自带运行时**的 QQ 组件"
                    "（NapCat.Shell.Windows.Node.zip 内含 QQ NT 9.9.32，"
                    "正是 NapCat v4.18.19 支持的版本线）。\n"
                    "自带运行时不需要管理员权限，也不会改动你已安装的 QQ。"
                ),
            )

        # 没有任何一条给出可用结果：挑信息量最大的一条报出来
        detail = ""
        for action, text in errors:
            if "不支持的api" not in text.lower():
                detail = f"{action}: {text}"
                break
        if not detail and errors:
            detail = f"{errors[0][0]}: {errors[0][1]}"

        raise NapCatError(
            f"无法获取下载直链：{detail or '接口未返回 url'}",
            hint="群文件可能已被删除或过期，也可能是 NapCat 版本差异，请更新组件。",
        )

    # -------------------------------------------------- 便捷组合

    def fetch_group_files(
        self,
        group_id: str,
        *,
        recursive: bool = True,
        page_size: int = 50,
    ) -> list[GroupFile]:
        return list(
            self.walk_group_files(
                group_id, recursive=recursive, page_size=page_size
            )
        )

    def wait_until_ready(self, timeout: float = 60.0, interval: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ping():
                return True
            time.sleep(interval)
        return False


@dataclass
class FakeOneBotClient:
    """离线测试/演示用的假客户端，行为与真客户端一致。"""

    files_by_group: dict[str, list[GroupFile]] = field(default_factory=dict)
    urls: dict[tuple[str, str], str] = field(default_factory=dict)
    online: bool = True
    url_action: str = "get_group_file_url"

    def get_login_info(self) -> LoginInfo:
        return LoginInfo(
            user_id="10001" if self.online else "",
            online=self.online,
            raw_ok=True,
            message="已登录" if self.online else "未登录",
        )

    def ping(self) -> bool:
        return True

    def fetch_group_files(self, group_id: str, **_kw: Any) -> list[GroupFile]:
        return list(self.files_by_group.get(str(group_id), []))

    def walk_group_files(self, group_id: str, **_kw: Any) -> Iterator[GroupFile]:
        yield from self.fetch_group_files(group_id)

    def get_group_file_url(self, group_id: str, file_id: str, busid: int = 102) -> str:
        url = self.urls.get((str(group_id), str(file_id)))
        if not url:
            raise NapCatError(f"假客户端未登记直链：{file_id}")
        return url
