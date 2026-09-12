"""NapCat / OneBot 接入层。"""

from .client import FakeOneBotClient, LoginInfo, OneBotClient, OneBotHTTPError
from .process import NAPCAT_RELEASES_URL, NapCatManager, NapCatStatus
from .qr import (
    LoginStatus,
    QrCode,
    check_login,
    fetch_qrcode,
    generate_qr_png,
    webui_url,
)

__all__ = [
    "OneBotClient",
    "OneBotHTTPError",
    "LoginInfo",
    "FakeOneBotClient",
    "NapCatManager",
    "NapCatStatus",
    "NAPCAT_RELEASES_URL",
    "QrCode",
    "LoginStatus",
    "check_login",
    "fetch_qrcode",
    "generate_qr_png",
    "webui_url",
]
