"""115 login service."""

import asyncio
import json
import logging
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from urllib.error import HTTPError

from server.common.exceptions import ConfigurationError, FolderNotFoundError, InvalidFolderError
from server.domain.system.config_service import ConfigService
from server.infrastructure.config import DATA_DIR
from server.models.cloud_115 import (
    Cloud115Account,
    Cloud115AccountInfo,
    Cloud115Config,
    Cloud115DeviceOption,
    Cloud115Membership,
    Cloud115QrSession,
    Cloud115QrStatus,
    Cloud115SessionDevice,
    Cloud115Status,
    Cloud115Storage,
)

logger = logging.getLogger(__name__)

# 登录类接口的候选域名，按此顺序逐个尝试（p115client 的
# AVAILABLE_PASSPORTAPI_BASE_URLS 就是为此用途提供的清单，这里只取 https 且把
# 实测可用的 hn 域名放前面）。2026-09-26 实测：qrcodeapi.115.com/get/status/
# 对所有参数恒返回 405（HTML 错误页），同一请求打到 hnqrcodeapi.115.com 正常
# （仍是 30s 长轮询），单域名硬编码会在 115 迁移域名期间整体失效。
LOGIN_API_BASE_URLS = (
    "https://hnqrcodeapi.115.com",
    "https://qrcodeapi.115.com",
)

# 115 令牌失效（过期）时 /get/status/ 的错误码：实测令牌有效期约 5 分钟，
# 过期后响应变成 {"state":0,"code":40199002,"message":"key invalid","data":{}}，
# 此时 data.status 缺失，不能当成「等待扫码」。
QRCODE_TOKEN_INVALID_CODES = (40199002,)

# 登录态失效（cookie 过期/被踢）时账号类接口的返回码：实测 2026-09-26 用
# ``UID=1; CID=2; SEID=3; KID=4`` 打 user_base_info / login_devices，两个登录域名
# 均返回 ``{"state":0,"code":40101032,"errno":40101032,"message":"请重新登录"}``
# （proapi 的接口则是 errno 99）。这类响应不抛异常，只能按码/文案判定，否则
# 「cookie 已失效」会与「网络故障」混成一类，界面分不出该不该让用户重新扫码。
SESSION_INVALID_CODES = (99, 40101032)

DEFAULT_APP = "alipaymini"
PROJECT_P115_HOME = DATA_DIR / "p115-home"
QRCODE_PAYLOAD_PREFIX = "cloud_115_qr_payload:"
VIRTUAL_115_ROOT_PATH = "/115网盘"
# Mirrors file_service.SUPPORTED_VIDEO_EXTENSIONS (kept local to avoid a circular import).
SCAN_VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv", ".rmvb", ".ts",
    ".m2ts", ".bdmv", ".webm", ".3gp", ".mpg", ".mpeg", ".vob", ".iso",
}
# 登录设备白名单：顺序即前端下拉顺序，仅保留实测可用的标准端。
# 说明：p115client 的 APP_TO_SSOENT 里还有一批别名（desktop/bios/bandroid/bipad/windows/mac/linux），
# 其中 desktop、bipad 实测已失效，其余均与下方标准端重复，故不再向前端暴露。
LOGIN_DEVICE_VALUES = (
    "web",
    "ios",
    "115ios",
    "android",
    "115android",
    "ipad",
    "115ipad",
    "tv",
    "apple_tv",
    "qandroid",
    "qios",
    "qipad",
    "os_windows",
    "os_mac",
    "os_linux",
    "wechatmini",
    "alipaymini",
    "harmony",
)
STANDARD_DEVICE_LABELS = {
    "web": "115生活_网页端",
    "ios": "115生活_苹果端",
    "115ios": "115_苹果端",
    "android": "115生活_安卓端",
    "115android": "115_安卓端",
    "ipad": "115生活_苹果平板端",
    "115ipad": "115_苹果平板端",
    "tv": "115生活_安卓电视端",
    "apple_tv": "115生活_苹果电视端",
    "qandroid": "115管理_安卓端",
    "qios": "115管理_苹果端",
    "qipad": "115管理_苹果平板端",
    "os_windows": "115生活_Windows端",
    "os_mac": "115生活_macOS端",
    "os_linux": "115生活_Linux端",
    "wechatmini": "115生活_微信小程序端",
    "alipaymini": "115生活_支付宝小程序",
    "harmony": "115_鸿蒙端",
}
STATUS_MESSAGES = {
    0: ("pending", "等待扫码"),
    1: ("scanned", "已扫码，等待确认"),
    2: ("success", "登录成功"),
    -1: ("expired", "二维码已过期"),
    -2: ("canceled", "二维码已取消"),
}
_P115_IMPORT_LOCK = asyncio.Lock()
_P115_IMPORT_SYNC_LOCK = threading.Lock()
_P115_MODULE_CACHE: tuple[Any, Any] | None = None


@contextmanager
def _temporary_p115_home():
    """Temporarily point HOME/USERPROFILE to a writable project path."""
    PROJECT_P115_HOME.mkdir(parents=True, exist_ok=True)
    previous_home = os.environ.get("HOME")
    previous_userprofile = os.environ.get("USERPROFILE")
    writable_home = str(PROJECT_P115_HOME)
    os.environ["HOME"] = writable_home
    os.environ["USERPROFILE"] = writable_home
    try:
        yield
    finally:
        if previous_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous_home
        if previous_userprofile is None:
            os.environ.pop("USERPROFILE", None)
        else:
            os.environ["USERPROFILE"] = previous_userprofile


def _load_p115client_sync() -> tuple[Any, Any]:
    """Synchronously import p115client once with temporary writable HOME settings."""
    global _P115_MODULE_CACHE
    if _P115_MODULE_CACHE is not None:
        return _P115_MODULE_CACHE

    with _P115_IMPORT_SYNC_LOCK:
        if _P115_MODULE_CACHE is not None:
            return _P115_MODULE_CACHE

        import importlib

        with _temporary_p115_home():
            p115_module = importlib.import_module("p115client")
            const_module = importlib.import_module("p115client.const")
        _P115_MODULE_CACHE = (p115_module, const_module)
        return _P115_MODULE_CACHE


async def _load_p115client() -> tuple[Any, Any]:
    """Safely import p115client once with temporary writable HOME settings."""
    if _P115_MODULE_CACHE is not None:
        return _P115_MODULE_CACHE

    async with _P115_IMPORT_LOCK:
        return _load_p115client_sync()


async def load_p115client() -> tuple[Any, Any]:
    """公开契约：加载 p115client 模块（供存储适配器复用）。"""
    return await _load_p115client()


class P115Service:
    """Service for managing 115 QR login and status."""

    def __init__(self, config_service: ConfigService):
        self.config_service = config_service

    async def _call_login_api(
        self,
        method: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """按候选域名调用 115 登录类接口，返回第一个成功的响应。

        115 的登录域名在迁移期会只活一个（实测 qrcodeapi 的 /get/status/ 已 405，
        hnqrcodeapi 正常），单域名硬编码会导致整个扫码登录静默失败；全部域名都失败时
        抛出最后一个错误，不把故障伪装成「等待扫码」。
        """
        last_error: Exception | None = None
        for base_url in LOGIN_API_BASE_URLS:
            try:
                return await method(*args, base_url=base_url, async_=True, **kwargs)
            except Exception as exc:  # noqa: BLE001 - 逐个域名重试，最终原样抛出
                last_error = exc
                logger.warning("115 登录接口 %s 调用失败，尝试下一个域名：%s", base_url, exc)
        assert last_error is not None
        raise last_error

    def list_login_devices(self) -> list[Cloud115DeviceOption]:
        """Return the supported login devices (whitelist order preserved)."""
        _, const_module = _load_p115client_sync()
        supported_apps = const_module.AVAILABLE_APPS

        return [
            Cloud115DeviceOption(
                value=value,
                label=STANDARD_DEVICE_LABELS.get(value, supported_apps.get(value, value)),
                group="standard",
            )
            for value in LOGIN_DEVICE_VALUES
            if value in supported_apps
        ]

    async def get_status(self) -> Cloud115Status:
        """Return persisted 115 login status."""
        config = await self.config_service.get_115_config()
        return Cloud115Status(
            enabled=config.enabled,
            app=config.app or DEFAULT_APP,
            is_logged_in=config.is_logged_in,
            updated_at=config.updated_at,
        )

    async def get_account_info(self) -> Cloud115AccountInfo:
        """拉取 115 账号详情（身份 / 会员 / 容量 / 当前设备），并顺带实测登录态。

        账号数据只来自两个接口：``user_base_info``（身份 + 会员 + 容量 + 设备数，
        实测一个响应就能拿到全部字段）和 ``login_devices``（当前会话所在设备）。
        设备列表是增强项，失败只丢这一块，不影响身份卡。

        与 :meth:`get_status` 的分工：get_status 是纯读库（浏览目录时每请求都调），
        本方法会打 115，只给「设置页打开 / 用户点刷新」用。

        每次调用独立构造 client，可并发调用。
        """
        checked_at = datetime.now()
        config = await self.config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            return Cloud115AccountInfo(is_logged_in=False, checked_at=checked_at)

        client = await self._load_p115_client_with_config(config)
        try:
            profile_response = await self._call_login_api(
                client.user_base_info,
                app=self._normalize_app(config.app),
            )
        except Exception as exc:  # noqa: BLE001 - 拉不到就降级成「读不到」，不能当未登录
            logger.warning("115 账号信息读取失败（%s）：%s", checked_at.isoformat(), exc)
            return Cloud115AccountInfo(
                is_logged_in=True,
                is_session_valid=None,
                message=self._describe_account_probe_error(exc),
                checked_at=checked_at,
            )

        if not self._is_response_ok(profile_response):
            session_invalid = self._is_session_invalid(profile_response)
            return Cloud115AccountInfo(
                is_logged_in=True,
                is_session_valid=False if session_invalid else None,
                message=(
                    "115 登录已失效，请重新扫码登录"
                    if session_invalid
                    else self._describe_account_response_error(profile_response)
                ),
                checked_at=checked_at,
            )

        data = profile_response.get("data") or {}
        return Cloud115AccountInfo(
            is_logged_in=True,
            is_session_valid=True,
            account=self._build_account(data),
            membership=self._build_membership(data),
            storage=self._build_storage(data),
            device=await self._load_current_session_device(client),
            checked_at=checked_at,
        )

    @staticmethod
    def _is_response_ok(response: dict[str, Any]) -> bool:
        """115 的 state 字段：非 0 即成功（实测成功时为 1 / true，失败为 0)。"""
        return bool(response.get("state"))

    def _is_session_invalid(self, response: dict[str, Any]) -> bool:
        """判断响应是否表示登录态已失效（115 原文「请重新登录」）。"""
        code = self._coerce_int(response.get("code"))
        if code is None:
            code = self._coerce_int(response.get("errno"))
        if code in SESSION_INVALID_CODES:
            return True
        message = str(response.get("message") or response.get("error") or "")
        return "重新登录" in message or "登录已失效" in message

    def _describe_account_probe_error(self, exc: Exception) -> str:
        """账号信息拉取失败时的提示（区分 HTTP 层故障与其它异常）。"""
        if isinstance(exc, HTTPError):
            return f"115 账号信息读取失败（HTTP {exc.code}），请稍后重试"
        return "115 账号信息读取失败，请稍后重试"

    def _describe_account_response_error(self, response: dict[str, Any]) -> str:
        """接口返回 state=0 但并非登录失效时的提示（把 115 原文透传出来）。"""
        code = self._coerce_int(response.get("code"))
        if code is None:
            code = self._coerce_int(response.get("errno"))
        message = str(response.get("message") or response.get("error") or "").strip()
        if not message:
            return "115 账号信息读取失败，请稍后重试"
        if code is None:
            return f"115 账号信息读取失败：{message}"
        return f"115 账号信息读取失败（{code}）：{message}"

    def _build_account(self, data: dict[str, Any]) -> Cloud115Account:
        """从 user_base_info 的 data 映射账号身份。"""
        face = data.get("face") if isinstance(data.get("face"), dict) else {}
        return Cloud115Account(
            user_id=self._pick_first(data, "user_id", "display_uid"),
            nickname=self._pick_first(data, "user_name"),
            avatar_url=self._pick_first(face, "face_l", "face_m", "face_s"),
            device_count=self._coerce_int(data.get("device")),
        )

    def _build_membership(self, data: dict[str, Any]) -> Cloud115Membership:
        """从 user_base_info 的 data 映射会员状态（等级名取 115 原文）。"""
        vip_info = data.get("vip_info") if isinstance(data.get("vip_info"), dict) else {}
        level_name = self._pick_first(vip_info, "level_name") or self._pick_first(data, "vip")
        expire_timestamp = self._coerce_int(vip_info.get("expire_time"))
        if expire_timestamp is None:
            expire_timestamp = self._coerce_int(data.get("expire"))
        expire_date = self._pick_first(vip_info, "expire_date")
        if expire_date is None and expire_timestamp:
            expire_date = datetime.fromtimestamp(expire_timestamp).strftime("%Y-%m-%d")
        is_forever = bool(vip_info.get("is_forever")) or bool(self._coerce_int(data.get("forever")))
        return Cloud115Membership(
            is_vip=bool(level_name),
            level_name=level_name,
            expire_date=expire_date,
            is_forever=is_forever,
        )

    def _build_storage(self, data: dict[str, Any]) -> Cloud115Storage:
        """从 user_base_info 的 data 映射容量（占用百分比由原始字节算出）。"""
        used_bytes = self._coerce_int(data.get("size_used_raw"))
        total_bytes = self._coerce_int(data.get("size_total_raw"))
        used_percent: float | None = None
        if used_bytes is not None and total_bytes:
            used_percent = round(used_bytes / total_bytes * 100, 2)
        return Cloud115Storage(
            used_bytes=used_bytes,
            total_bytes=total_bytes,
            used_text=self._pick_first(data, "size_used"),
            total_text=self._pick_first(data, "size_total"),
            used_percent=used_percent,
        )

    async def _load_current_session_device(self, client: Any) -> Cloud115SessionDevice | None:
        """读取 115 登录设备列表里 is_current 的那台；读不到就不展示这一块。"""
        try:
            response = await self._call_login_api(client.login_devices)
        except Exception as exc:  # noqa: BLE001 - 设备信息是增强项，失败不影响账号卡
            logger.warning("115 登录设备读取失败：%s", exc)
            return None
        if not self._is_response_ok(response):
            return None

        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        rows = data.get("list") if isinstance(data.get("list"), list) else []
        current = next(
            (row for row in rows if isinstance(row, dict) and row.get("is_current")),
            None,
        )
        if current is None:
            current = data.get("last") if isinstance(data.get("last"), dict) else None
        if current is None:
            return None
        return Cloud115SessionDevice(
            name=self._pick_first(current, "name", "device"),
            ip=self._pick_first(current, "ip"),
            city=self._pick_first(current, "city"),
            login_at=self._format_epoch_datetime(current.get("utime")),
            is_unusual=bool(self._coerce_int(current.get("is_unusual"))),
        )

    def _format_epoch_datetime(self, value: Any) -> datetime | None:
        """把秒级时间戳转成 datetime（115 的 utime 字段）。"""
        timestamp = self._coerce_int(value)
        if timestamp is None:
            return None
        try:
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return None

    async def clear_login_state(self) -> None:
        """Clear persisted 115 login config and any pending QR sessions."""
        await self.config_service.delete_115_config()
        await self._delete_all_qr_payloads()

    async def start_qr_login(self, app: str) -> Cloud115QrSession:
        """Start a QR login session.

        同时清掉之前的二维码会话：同一时刻只应存在一个待扫码会话；被用户直接
        关掉弹窗的会话行没有其它回收时机（115 也不会主动叫我们删）。
        """
        normalized_app = self._normalize_app(app)
        p115_module, _ = await _load_p115client()
        await self._delete_all_qr_payloads()
        token_response = await self._call_login_api(
            p115_module.P115Client.login_qrcode_token,
            app=normalized_app,
        )
        token_data = token_response["data"]
        uid = token_data["uid"]
        qrcode_url = token_data.get("qrcode") or f"https://115.com/scan/dg-{uid}"
        await self._save_qr_payload(uid, token_data, normalized_app)
        return Cloud115QrSession(uid=uid, qrcode_url=qrcode_url, app=normalized_app)

    async def poll_qr_login(self, uid: str, app: str) -> Cloud115QrStatus:
        """Poll QR login status and persist cookies after success."""
        token_payload = await self._get_qr_payload(uid)
        normalized_app = self._resolve_qr_app(token_payload, app)
        if not self._has_complete_qr_payload(token_payload):
            await self._delete_qr_payload(uid)
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status="expired",
                message="二维码登录会话不存在或已过期，请重新扫码",
                is_logged_in=False,
            )
        p115_module, _ = await _load_p115client()

        status_response = await self._call_login_api(
            p115_module.P115Client.login_qrcode_scan_status,
            self._build_scan_status_payload(uid, token_payload),
        )
        raw_status = status_response.get("data", {}).get("status")
        if raw_status is None:
            # 状态未变化时长轮询会返回空 data（实测挂起约 30s），语义是「仍在
            # 等待扫码」；而令牌过期（约 5 分钟）同样是 data 为空，但外层带上
            # 错误码——两者必须分开，否则界面会永远停在「等待扫码」，用户扫到的
            # 却是已失效的二维码（手机端提示二维码无效）。
            if self._is_qr_token_invalid(status_response):
                await self._delete_qr_payload(uid)
                return Cloud115QrStatus(
                    uid=uid,
                    app=normalized_app,
                    status="expired",
                    message="二维码已过期，请重新生成",
                    is_logged_in=False,
                )
            if status_response.get("state") == 0:
                # 其他失败原因：把 115 的原文透传出来，不假装成「等待扫码」
                return Cloud115QrStatus(
                    uid=uid,
                    app=normalized_app,
                    status="unknown",
                    message=self._describe_status_error(status_response),
                    is_logged_in=False,
                )
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status="pending",
                message=STATUS_MESSAGES[0][1],
                is_logged_in=False,
            )
        status, message = STATUS_MESSAGES.get(raw_status, ("unknown", "未知登录状态"))

        if raw_status != 2:
            if raw_status in {-1, -2}:
                await self._delete_qr_payload(uid)
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status=status,
                message=message,
                is_logged_in=False,
            )

        result_response = await self._call_login_api(
            p115_module.P115Client.login_qrcode_scan_result,
            uid,
            app=normalized_app,
        )
        cookies = self._extract_cookies(result_response)
        if not cookies:
            return Cloud115QrStatus(
                uid=uid,
                app=normalized_app,
                status="scanned",
                message="已扫码确认，但未获取到登录 cookies",
                is_logged_in=False,
            )

        await self.config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app=normalized_app,
                cookies=cookies,
                is_logged_in=bool(cookies),
                updated_at=datetime.now(),
            )
        )
        await self._delete_qr_payload(uid)

        return Cloud115QrStatus(
            uid=uid,
            app=normalized_app,
            status="success",
            message=message,
            is_logged_in=True,
        )

    async def browse(
        self,
        *,
        path: str = VIRTUAL_115_ROOT_PATH,
        file_id: str | None = "0",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """Browse 115 directory contents with an async-only API."""
        return await self._browse_async(
            path=path,
            file_id=file_id,
            page=page,
            page_size=page_size,
        )

    async def scan_folder(
        self,
        *,
        path: str = VIRTUAL_115_ROOT_PATH,
        file_id: str | None = "0",
    ) -> list[dict[str, Any]]:
        """Recursively scan a 115 directory for video files.

        Unlike :meth:`browse`, this walks every sub-directory and returns a flat
        list of video-file entries (no pagination). Each entry keeps the same
        shape produced by :meth:`browse` (name/path/is_dir/provider/file_id/
        parent_id/size/mtime) so callers can rebuild a :class:`StorageLocator`.
        """
        config = await self.config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            raise ConfigurationError("请先登录 115 网盘", config_key="cloud_115_config")

        normalized_path = self._normalize_virtual_path(path)
        client = await self._load_p115_client_with_config(config)
        root_directory_id = await self._resolve_directory_id(
            client=client,
            path=normalized_path,
            file_id=file_id,
        )
        collected: list[dict[str, Any]] = []
        await self._scan_recursive(
            client=client,
            directory_id=root_directory_id,
            current_path=normalized_path,
            collected=collected,
        )
        return collected

    async def _scan_recursive(
        self,
        *,
        client: Any,
        directory_id: str,
        current_path: str,
        collected: list[dict[str, Any]],
    ) -> None:
        """Depth-first scan collecting video files and recursing into folders."""
        page_size = 100
        offset = 0
        while True:
            response = await self._call_browse_api(
                client.fs_files,
                {
                    "cid": directory_id,
                    "offset": offset,
                    "limit": page_size,
                    "show_dir": 1,
                },
                async_=True,
                error_path=current_path,
            )
            rows = response.get("data", []) or []
            if not rows:
                break

            current_path_resolved, _parent_path, _current_fid, _parent_fid = await self._call_browse_api(
                self._resolve_browse_paths,
                client=client,
                response=response,
                directory_id=directory_id,
                requested_path=current_path,
                error_path=current_path,
            )

            for row in rows:
                if not isinstance(row, dict):
                    continue
                entry = self._normalize_browse_entry(row, current_path_resolved)
                if entry["is_dir"]:
                    child_id = entry.get("file_id") or "0"
                    await self._scan_recursive(
                        client=client,
                        directory_id=child_id,
                        current_path=entry["path"],
                        collected=collected,
                    )
                else:
                    if self._is_video_filename(entry.get("name") or ""):
                        collected.append(entry)

            # Stop when the page is not full (last page) or total is exhausted.
            if len(rows) < page_size:
                break
            offset += page_size

    @staticmethod
    def _is_video_filename(name: str) -> bool:
        """Return True if the filename looks like a supported video file."""
        dot = name.rfind(".")
        if dot < 0:
            return False
        return name[dot:].lower() in SCAN_VIDEO_EXTENSIONS

    async def _load_p115_client_with_config(self, config: Cloud115Config) -> Any:
        """Build a configured P115Client from persisted login config.

        只传 cookies/app/console_qrcode：pinned 版本（requirements.txt 的
        0.0.9.6.5.1）的 __init__ 只有这四个参数，旧的 check_for_relogin /
        ensure_cookies 已从库中删除，多传即 TypeError（2026-09-26 实测：登录成功后
        浏览 115 一律 500）。新版 init 只把 cookies 写进 cookie jar，不校验、不重登，
        且 cookies 传非 None 时不会触发扫码登录；凭证有效性由首个请求的响应判定。
        """
        p115_module, _ = await _load_p115client()
        return p115_module.P115Client(
            config.cookies,
            app=self._normalize_app(config.app),
            console_qrcode=False,
        )

    def _build_login_expired_error(self) -> ConfigurationError:
        """Return a user-facing error for expired 115 login state."""
        return ConfigurationError(
            "115 登录已失效，请重新扫码登录",
            config_key="cloud_115_config",
        )

    def _build_generic_browse_error(self) -> ConfigurationError:
        """Return a generic provider browse failure error."""
        return ConfigurationError(
            "115 网盘目录浏览失败，请稍后重试",
            config_key="cloud_115_config",
        )

    def _build_provider_http_error(self, exc: HTTPError) -> ConfigurationError:
        """把 115 接口的 HTTP 错误码翻译成可诊断的提示。

        2026-09-26 实测：短时间内集中请求（探针并发 6 递归扫描）后，115 会对
        fs_files 恒返回 HTTP 405（HTML 错误页），持续数分钟；此时归为「浏览失败，
        请稍后重试」会把限流伪装成未知故障，看不出是「发得太密」还是「登录态坏了」。
        """
        reason = exc.reason.strip() if isinstance(exc.reason, str) else ""
        if exc.code == 405:
            detail = "115 接口暂时拒绝请求（HTTP 405，多为请求过于频繁）"
        else:
            suffix = f"（{reason}）" if reason else ""
            detail = f"115 接口返回 HTTP {exc.code}{suffix}"
        return ConfigurationError(
            f"{detail}，请稍后重试",
            config_key="cloud_115_config",
        )

    def _map_browse_error(self, exc: Exception, path: str) -> Exception:
        """Translate provider exceptions into project-level browse semantics."""
        if isinstance(exc, (ConfigurationError, FolderNotFoundError, InvalidFolderError)):
            return exc

        exc_name = type(exc).__name__
        message = str(exc)
        normalized_message = message.lower()

        if exc_name in {
            "P115AuthenticationError",
            "P115LoginError",
            "P115AccessTokenError",
            "P115OpenAppAuthLimitExceeded",
        }:
            return self._build_login_expired_error()

        if isinstance(exc, FileNotFoundError):
            return FolderNotFoundError(path)

        if exc_name in {"P115InvalidArgumentError"} or isinstance(exc, ValueError):
            return InvalidFolderError(path, reason="115 网盘目录路径无效")

        if isinstance(exc, HTTPError):
            return self._build_provider_http_error(exc)

        if "expired" in normalized_message or "cookie" in normalized_message:
            return self._build_login_expired_error()

        if "not found" in normalized_message or "missing" in normalized_message:
            return FolderNotFoundError(path)

        if "invalid" in normalized_message or "bad path" in normalized_message:
            return InvalidFolderError(path, reason="115 网盘目录路径无效")

        return self._build_generic_browse_error()

    async def _call_browse_api(self, func, *args, error_path: str, **kwargs):
        """Wrap p115client browse calls and translate third-party failures."""
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            raise self._map_browse_error(exc, error_path) from exc

    def _virtual_path_to_115_path(self, path: str) -> str:
        """Convert a virtual 115 path into the provider-native directory path."""
        normalized_path = self._normalize_virtual_path(path)
        if normalized_path == VIRTUAL_115_ROOT_PATH:
            return "/"
        suffix = normalized_path.removeprefix(VIRTUAL_115_ROOT_PATH)
        return suffix or "/"

    def _extract_directory_id(self, response: Any) -> str | None:
        """Extract a directory id from fs_dir_getid/getid2 responses."""
        if isinstance(response, dict):
            for key in ("id", "file_id", "cid"):
                value = response.get(key)
                if value not in (None, ""):
                    return str(value)

            data = response.get("data")
            if isinstance(data, dict):
                for key in ("id", "file_id", "cid"):
                    value = data.get(key)
                    if value not in (None, ""):
                        return str(value)
        return None

    async def _resolve_directory_id(
        self,
        *,
        client: Any,
        path: str,
        file_id: str | None,
    ) -> str:
        """Resolve the effective 115 directory id for a browse request."""
        if file_id:
            return str(file_id)

        normalized_path = self._normalize_virtual_path(path)
        if normalized_path == VIRTUAL_115_ROOT_PATH:
            return "0"

        native_path = self._virtual_path_to_115_path(path)
        response = await self._call_browse_api(
            client.fs_dir_getid,
            native_path,
            async_=True,
            error_path=normalized_path,
        )
        directory_id = self._extract_directory_id(response)
        if directory_id:
            return directory_id

        response = await self._call_browse_api(
            client.fs_dir_getid2,
            native_path,
            async_=True,
            error_path=normalized_path,
        )
        directory_id = self._extract_directory_id(response)
        if directory_id:
            return directory_id

        raise FolderNotFoundError(normalized_path)

    async def resolve_directory_id_by_path(self, path: str) -> str:
        """按虚拟路径解析 115 目录 id（自动加载已登录客户端）。

        公开契约：供监控配置同步等上层用例调用。
        """
        config = await self.config_service.get_115_config()
        if not config.is_logged_in:
            raise ValueError("请先登录 115 网盘")
        client = await self._load_p115_client_with_config(config)
        return await self._resolve_directory_id(client=client, path=path, file_id=None)

    async def load_logged_in_client(self) -> Any:
        """加载已登录的 115 客户端；未登录时抛 ValueError。

        公开契约：供事件监控等需要原始客户端的场景复用。
        """
        config = await self.config_service.get_115_config()
        if not config.is_logged_in:
            raise ValueError("115 未登录")
        return await self._load_p115_client_with_config(config)

    async def _browse_async(
        self,
        *,
        path: str,
        file_id: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        """Load one page of 115 directory entries using persisted cookies."""
        config = await self.config_service.get_115_config()
        if not config.is_logged_in or not config.cookies.strip():
            raise ConfigurationError("请先登录 115 网盘", config_key="cloud_115_config")

        client = await self._load_p115_client_with_config(config)
        normalized_path = self._normalize_virtual_path(path)
        directory_id = await self._resolve_directory_id(
            client=client,
            path=normalized_path,
            file_id=file_id,
        )
        response = await self._call_browse_api(
            client.fs_files,
            {
                "cid": directory_id,
                "offset": max(page - 1, 0) * page_size,
                "limit": page_size,
                "show_dir": 1,
            },
            async_=True,
            error_path=normalized_path,
        )
        current_path, parent_path, current_file_id, parent_file_id = await self._call_browse_api(
            self._resolve_browse_paths,
            client=client,
            response=response,
            directory_id=directory_id,
            requested_path=normalized_path,
            error_path=normalized_path,
        )
        entries = [
            self._normalize_browse_entry(item, current_path)
            for item in response.get("data", [])
            if isinstance(item, dict)
        ]
        return {
            "current_path": current_path,
            "parent_path": parent_path,
            "current_file_id": current_file_id,
            "parent_file_id": parent_file_id,
            "entries": entries,
            "total": self._coerce_int(response.get("count"), len(entries)),
        }

    async def _get_qr_payload(self, uid: str) -> dict[str, Any]:
        """Return the saved QR payload required by p115client."""
        value = await self.config_service.get(self._qr_payload_key(uid), encrypted=True)
        if value:
            try:
                data = json.loads(value)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return {"uid": uid}

    async def _save_qr_payload(
        self,
        uid: str,
        payload: dict[str, Any],
        app: str,
    ) -> None:
        """Persist QR payload so polling survives new service instances."""
        persisted_payload = dict(payload)
        persisted_payload["app"] = app
        await self.config_service.set(
            self._qr_payload_key(uid),
            json.dumps(persisted_payload),
            encrypted=True,
        )

    async def _delete_qr_payload(self, uid: str) -> None:
        """Remove persisted QR payload after successful login."""
        await self.config_service.delete(self._qr_payload_key(uid))

    async def _delete_all_qr_payloads(self) -> None:
        """Remove every persisted QR payload so login cannot resume after logout."""
        await self.config_service.delete_by_prefix(QRCODE_PAYLOAD_PREFIX)

    def _is_qr_token_invalid(self, response: dict[str, Any]) -> bool:
        """判断状态响应是否表示二维码令牌已失效（过期）。"""
        if response.get("state") != 0:
            return False
        code = response.get("code")
        return code in QRCODE_TOKEN_INVALID_CODES

    def _describe_status_error(self, response: dict[str, Any]) -> str:
        """把 115 的状态查询错误原文整理成给用户的提示。"""
        message = str(response.get("message") or "").strip()
        code = response.get("code")
        if message:
            return f"二维码状态查询失败：{message}" if code is None else f"二维码状态查询失败（{code}）：{message}"
        return "二维码状态查询失败，请重新生成二维码"

    def _extract_cookies(self, result_response: dict[str, Any]) -> str:
        """Extract cookies from p115client QR login response."""
        cookie_data = result_response.get("data", {}).get("cookie")
        if isinstance(cookie_data, dict):
            parts = []
            for key in ("UID", "CID", "SEID", "KID"):
                value = cookie_data.get(key)
                if value:
                    parts.append(f"{key}={value}")
            if parts:
                return "; ".join(parts)
        if isinstance(cookie_data, str):
            return cookie_data.strip().rstrip(";")
        return ""

    def _qr_payload_key(self, uid: str) -> str:
        """Build storage key for a QR login payload."""
        return f"{QRCODE_PAYLOAD_PREFIX}{uid}"

    def _build_scan_status_payload(self, uid: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Return only token fields required for scan-status polling."""
        status_payload = {key: value for key, value in payload.items() if key != "app"}
        return status_payload or {"uid": uid}

    def _has_complete_qr_payload(self, payload: dict[str, Any]) -> bool:
        """Check whether the persisted QR payload has the required token fields."""
        required_keys = ("uid", "time", "sign")
        return all(payload.get(key) for key in required_keys)

    def _resolve_qr_app(self, payload: dict[str, Any], app: str | None) -> str:
        """Prefer the persisted canonical app when resuming QR polling."""
        persisted_app = payload.get("app")
        if isinstance(persisted_app, str) and persisted_app.strip():
            return self._normalize_app(persisted_app)
        return self._normalize_app(app)

    async def _resolve_browse_paths(
        self,
        *,
        client: Any,
        response: dict[str, Any],
        directory_id: str,
        requested_path: str,
    ) -> tuple[str, str | None, str | None, str | None]:
        """Resolve current/parent virtual paths and their file ids from breadcrumbs.

        Returns ``(current_path, parent_path, current_file_id, parent_file_id)``.
        The file ids let callers navigate back to the parent directory without
        keeping a client-side navigation stack.
        """
        breadcrumbs = self._extract_fs_files_breadcrumbs(response)
        if not breadcrumbs and directory_id != "0":
            info = await client.fs_info({"file_id": directory_id}, async_=True)
            breadcrumbs = self._extract_fs_info_breadcrumbs(info)

        current_path = self._breadcrumbs_to_virtual_path(breadcrumbs)
        if not current_path:
            current_path = self._normalize_virtual_path(requested_path)
        if not current_path:
            current_path = VIRTUAL_115_ROOT_PATH

        # 当前目录 file_id：优先面包屑最后一层，否则用请求的 directory_id
        current_file_id: str | None = None
        if breadcrumbs:
            current_file_id = breadcrumbs[-1].get("cid") or str(directory_id)
        else:
            current_file_id = str(directory_id)

        if current_path == VIRTUAL_115_ROOT_PATH:
            # 115 根的上级是本地根（parent_path=None 表示触顶）
            return current_path, None, current_file_id, None

        parent_path = current_path.rsplit("/", 1)[0]
        if not parent_path or parent_path == "/":
            parent_path = VIRTUAL_115_ROOT_PATH

        # 父目录 file_id：面包屑倒数第二个；115 根的父 id 固定为 "0"
        parent_file_id: str | None = None
        if len(breadcrumbs) >= 2:
            parent_file_id = breadcrumbs[-2].get("cid") or "0"
        elif parent_path == VIRTUAL_115_ROOT_PATH:
            parent_file_id = "0"
        return current_path, parent_path, current_file_id, parent_file_id

    def _extract_fs_files_breadcrumbs(self, response: dict[str, Any]) -> list[dict[str, str]]:
        """Translate fs_files path payload into simple breadcrumbs."""
        path_rows = response.get("path")
        if not isinstance(path_rows, list):
            return []
        breadcrumbs: list[dict[str, str]] = []
        for row in path_rows:
            if not isinstance(row, dict):
                continue
            cid = self._pick_first(row, "cid", "file_id", "id")
            if cid is None:
                continue
            breadcrumbs.append(
                {
                    "cid": cid,
                    "pid": self._pick_first(row, "pid", "parent_id") or "0",
                    "name": self._pick_first(row, "name", "file_name", "n") or "",
                }
            )
        return breadcrumbs

    def _extract_fs_info_breadcrumbs(self, response: dict[str, Any]) -> list[dict[str, str]]:
        """Translate fs_info payload into breadcrumbs when fs_files omits them."""
        data = response.get("data", response)
        if not isinstance(data, dict):
            return []
        path_rows = data.get("paths")
        if not isinstance(path_rows, list):
            return []

        breadcrumbs: list[dict[str, str]] = []
        parent_id = "0"
        for row in path_rows:
            if not isinstance(row, dict):
                continue
            cid = self._pick_first(row, "file_id", "cid", "id")
            if cid is None:
                continue
            breadcrumbs.append(
                {
                    "cid": cid,
                    "pid": parent_id,
                    "name": self._pick_first(row, "file_name", "name", "n") or "",
                }
            )
            parent_id = cid

        current_id = self._pick_first(data, "file_id", "cid", "id")
        if (
            current_id is not None
            and breadcrumbs
            and breadcrumbs[-1]["cid"] != current_id
        ):
            breadcrumbs.append(
                {
                    "cid": current_id,
                    "pid": parent_id,
                    "name": self._pick_first(data, "file_name", "name", "n") or "",
                }
            )
        return breadcrumbs

    def _breadcrumbs_to_virtual_path(self, breadcrumbs: list[dict[str, str]]) -> str:
        """Build a virtual 115 path from breadcrumb rows.

        The 115 cloud root (cid == "0") is reported with a display name like
        "根目录", but it IS the virtual root already represented by the
        ``/115网盘`` prefix, so its name must not be appended to the path.
        """
        parts = [
            row["name"]
            for row in breadcrumbs
            if row.get("name") and str(row.get("cid")) != "0"
        ]
        if not parts:
            return VIRTUAL_115_ROOT_PATH if breadcrumbs else ""
        return f"{VIRTUAL_115_ROOT_PATH}/{'/'.join(parts)}"

    def _normalize_browse_entry(
        self,
        entry: dict[str, Any],
        current_path: str,
    ) -> dict[str, Any]:
        """Map raw 115 rows into FileService-compatible entry dicts."""
        is_dir = "fid" not in entry
        file_id = self._pick_first(entry, "cid" if is_dir else "fid", "id") or "0"
        parent_id = self._pick_first(entry, "pid" if is_dir else "cid", "parent_id")
        name = self._pick_first(entry, "n", "name", "file_name") or ""

        return {
            "name": name,
            "path": self._join_virtual_path(current_path, name),
            "is_dir": is_dir,
            "provider": "115",
            "file_id": file_id,
            "parent_id": parent_id,
            "size": None if is_dir else self._coerce_int(entry.get("s") or entry.get("size")),
            "mtime": self._format_timestamp(
                entry.get("te") or entry.get("mtime") or entry.get("user_utime")
            ),
        }

    def _normalize_virtual_path(self, path: str | None) -> str:
        """Normalize incoming provider paths to the virtual 115 root namespace."""
        value = (path or "").strip()
        if not value or value == "/":
            return VIRTUAL_115_ROOT_PATH
        if value == "115网盘":
            return VIRTUAL_115_ROOT_PATH
        if value.startswith(VIRTUAL_115_ROOT_PATH):
            return value.rstrip("/") or VIRTUAL_115_ROOT_PATH
        return f"{VIRTUAL_115_ROOT_PATH}/{value.lstrip('/')}".rstrip("/")

    def _join_virtual_path(self, current_path: str, name: str) -> str:
        """Join a child name onto the current virtual path."""
        base = current_path.rstrip("/") or VIRTUAL_115_ROOT_PATH
        child = name.strip("/")
        return f"{base}/{child}" if child else base

    def _coerce_int(self, value: Any, default: int | None = None) -> int | None:
        """Convert loosely typed numeric values into ints."""
        if value in (None, ""):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def _format_timestamp(self, value: Any) -> str | None:
        """Convert epoch-second timestamps into ISO strings."""
        timestamp = self._coerce_int(value)
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp).isoformat()

    def _pick_first(self, payload: dict[str, Any], *keys: str) -> str | None:
        """Return the first non-empty string-like value for the given keys."""
        for key in keys:
            value = payload.get(key)
            if value in (None, ""):
                continue
            return str(value)
        return None

    def _normalize_app(self, app: str | None) -> str:
        """Normalize aliases to canonical app identifiers.

        仅供存量配置与内置调用兜底：新配置只会写入标准端，别名已从下拉移除。
        """
        value = (app or DEFAULT_APP).strip() or DEFAULT_APP
        if value == "desktop":
            return "web"
        if value == "windows":
            return "os_windows"
        if value == "mac":
            return "os_mac"
        if value == "linux":
            return "os_linux"
        return value
