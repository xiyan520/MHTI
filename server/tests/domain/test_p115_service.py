"""Unit tests for P115Service."""

import os
from datetime import datetime
from types import SimpleNamespace
from urllib.error import HTTPError

import aiosqlite
import pytest

import server.domain.integration.p115_service as p115_service_module
from server.bootstrap import get_container, get_service
from server.common.exceptions import ConfigurationError, FolderNotFoundError, InvalidFolderError
from server.common.service_names import Services
from server.domain.integration.p115_service import P115Service
from server.domain.system.config_service import ConfigService
from server.models.cloud_115 import Cloud115Config


@pytest.fixture
def p115_service(config_service: ConfigService) -> P115Service:
    """Provide a P115Service instance with test config storage."""
    return P115Service(config_service=config_service)


class FakeP115Client:
    """Minimal fake p115 client for QR login tests."""

    init_calls: list[dict] = []
    token_calls: list[dict[str, str | None]] = []
    status_calls: list[dict] = []
    result_calls: list[dict] = []
    fs_files_calls: list[dict] = []
    fs_info_calls: list[dict] = []
    fs_dir_getid_calls: list[dict] = []
    fs_files_response: dict = {"path": [{"cid": "0", "pid": "0", "name": ""}], "count": 0, "data": []}
    fs_info_response: dict = {
        "data": {
            "file_id": "0",
            "file_name": "",
            "sha1": "",
            "paths": [{"file_id": "0", "file_name": ""}],
        }
    }
    fs_dir_getid_response: dict = {"id": "0"}
    user_base_info_calls: list[dict] = []
    login_devices_calls: list[dict] = []
    # 真实响应形状（2026-09-26 用登录态实测 user_base_info / login_devices 后抄录）
    user_base_info_response: dict = {
        "state": 1,
        "code": 0,
        "message": "",
        "data": {
            "user_id": 336320871,
            "display_uid": "336320871",
            "user_name": "测试账号",
            "face": {
                "face_l": "https://avatars.115.com/01/xxx_l.jpg",
                "face_m": "https://avatars.115.com/01/xxx_m.jpg",
                "face_s": "https://avatars.115.com/01/xxx_s.jpg",
            },
            "size_used_raw": "37474186538573",
            "size_total_raw": "75136879795860",
            "size_used": "34.08TB",
            "size_total": "68.34TB",
            "vip": "年费VIP",
            "expire": 1846042672,
            "forever": 0,
            "device": 8,
            "vip_info": {
                "level_name": "年费VIP",
                "expire_time": 1846042672,
                "expire_date": "2028-07-01",
                "is_forever": False,
            },
        },
    }
    login_devices_response: dict = {
        "state": 1,
        "code": 0,
        "message": "",
        "data": {
            "last": {
                "ip": "223.64.***.39",
                "device": "115_鸿蒙端",
                "city": "江苏",
                "utime": 1790435158,
            },
            "list": [
                {
                    "is_current": 1,
                    "ssoent": "S1",
                    "utime": 1790435158,
                    "device": "",
                    "name": "115_鸿蒙端",
                    "icon": "harmony",
                    "ip": "223.64.***.39",
                    "city": "江苏",
                    "is_unusual": 0,
                },
                {
                    "is_current": 0,
                    "ssoent": "A1",
                    "utime": 1790386792,
                    "device": "Chrome",
                    "name": "115生活_网页端",
                    "icon": "web",
                    "ip": "223.64.***.39",
                    "city": "江苏",
                    "is_unusual": 0,
                },
            ],
        },
    }
    user_base_info_error: Exception | None = None
    login_devices_error: Exception | None = None

    @staticmethod
    async def _user_base_info_response(app: str) -> dict:
        return FakeP115Client.user_base_info_response

    def user_base_info(
        self,
        /,
        app: str = "web",
        base_url: str = "",
        async_: bool = False,
    ) -> dict:
        FakeP115Client.user_base_info_calls.append(
            {"app": app, "base_url": base_url, "async_": async_}
        )
        if FakeP115Client.user_base_info_error is not None:
            raise FakeP115Client.user_base_info_error
        if async_:
            return FakeP115Client._user_base_info_response(app)
        return FakeP115Client.user_base_info_response

    @staticmethod
    async def _login_devices_response() -> dict:
        return FakeP115Client.login_devices_response

    def login_devices(
        self,
        /,
        app: str = "web",
        base_url: str = "",
        async_: bool = False,
    ) -> dict:
        FakeP115Client.login_devices_calls.append(
            {"app": app, "base_url": base_url, "async_": async_}
        )
        if FakeP115Client.login_devices_error is not None:
            raise FakeP115Client.login_devices_error
        if async_:
            return FakeP115Client._login_devices_response()
        return FakeP115Client.login_devices_response

    def __init__(
        self,
        cookies: str = "",
        app: str = "",
        app_id: int = 0,
        console_qrcode: bool = True,
    ) -> None:
        """签名对齐 pinned 版本（0.0.9.6.5.1）的 P115Client.__init__。

        旧版参数（check_for_relogin / ensure_cookies）不得再传入：假客户端多收一个
        关键字不会报错，而真实客户端会抛 TypeError，签名收紧后才能拦住这类回归。
        """
        self.cookies_str = SimpleNamespace(cookies=cookies)
        self.cookies: dict[str, str] = {}
        FakeP115Client.init_calls.append(
            {
                "cookies": cookies,
                "app": app,
                "app_id": app_id,
                "console_qrcode": console_qrcode,
            }
        )

    @staticmethod
    async def _token_response(app: str) -> dict:
        return {
            "data": {
                "uid": "uid-123",
                "time": 1710000000,
                "sign": "sign-123",
                "qrcode": "https://115.com/scan/dg-uid-123",
            }
        }

    def login_qrcode_token(
        self=None,
        /,
        app: str = "web",
        base_url: str = "",
        async_: bool = False,
    ) -> dict:
        FakeP115Client.token_calls.append(
            {"self": self, "app": app, "base_url": base_url, "async_": async_}
        )
        if async_:
            return FakeP115Client._token_response(app)
        return {
            "data": {
                "uid": "uid-123",
                "time": 1710000000,
                "sign": "sign-123",
                "qrcode": "https://115.com/scan/dg-uid-123",
            }
        }

    @staticmethod
    async def _status_response(payload: dict) -> dict:
        assert payload["uid"] == "uid-123"
        assert payload["sign"] == "sign-123"
        return {"data": {"status": 2}}

    def login_qrcode_scan_status(
        payload: dict,
        base_url: str = "",
        async_: bool = False,
    ) -> dict:
        FakeP115Client.status_calls.append(
            {"payload": dict(payload), "base_url": base_url, "async_": async_}
        )
        if async_:
            return FakeP115Client._status_response(payload)
        return {"data": {"status": 2}}

    @staticmethod
    async def _result_response(uid: str, app: str, cookies=None) -> dict:
        assert uid == "uid-123"
        assert app == "alipaymini"
        return {
            "data": {
                "status": 2,
                "cookie": {
                    "UID": "1",
                    "CID": "2",
                    "SEID": "3",
                },
            }
        }

    def login_qrcode_scan_result(
        uid: str,
        app: str,
        cookies=None,
        base_url: str = "",
        async_: bool = False,
    ) -> dict:
        FakeP115Client.result_calls.append(
            {
                "uid": uid,
                "app": app,
                "cookies": cookies,
                "base_url": base_url,
                "async_": async_,
            }
        )
        if async_:
            return FakeP115Client._result_response(uid, app, cookies=cookies)
        return {
            "data": {
                "status": 2,
                "cookie": {
                    "UID": "1",
                    "CID": "2",
                    "SEID": "3",
                },
            }
        }

    @staticmethod
    async def _fs_files_response(payload: dict) -> dict:
        return FakeP115Client.fs_files_response

    def fs_files(self, payload: dict, async_: bool = False) -> dict:
        FakeP115Client.fs_files_calls.append({"payload": dict(payload), "async_": async_})
        if async_:
            return type(self)._fs_files_response(payload)
        return FakeP115Client.fs_files_response

    @staticmethod
    async def _fs_info_response(payload: dict) -> dict:
        return FakeP115Client.fs_info_response

    def fs_info(self, payload: dict, async_: bool = False) -> dict:
        FakeP115Client.fs_info_calls.append({"payload": dict(payload), "async_": async_})
        if async_:
            return type(self)._fs_info_response(payload)
        return FakeP115Client.fs_info_response

    @staticmethod
    async def _fs_dir_getid_response(payload: str | dict) -> dict:
        return FakeP115Client.fs_dir_getid_response

    def fs_dir_getid(self, payload: str | dict, async_: bool = False, **kwargs) -> dict:
        FakeP115Client.fs_dir_getid_calls.append(
            {"payload": payload, "async_": async_, "kwargs": dict(kwargs)}
        )
        if async_:
            return type(self)._fs_dir_getid_response(payload)
        return FakeP115Client.fs_dir_getid_response


def build_fake_p115_module() -> tuple[SimpleNamespace, SimpleNamespace]:
    """Build fake p115 module and const module."""
    const_module = SimpleNamespace(
        AVAILABLE_APPS={
            "web": "115生活_网页端",
            "ios": "115生活_苹果端",
            "alipaymini": "115生活_支付宝小程序",
            "os_windows": "115生活_Windows端",
            "os_mac": "115生活_macOS端",
            "os_linux": "115生活_Linux端",
        },
        APP_TO_SSOENT={
            "web": "A1",
            "desktop": "A1",
            "ios": "D1",
            "bios": "D2",
            "android": "F1",
            "bandroid": "F2",
            "ipad": "H1",
            "bipad": "H2",
            "os_windows": "P1",
            "windows": "P1",
            "os_mac": "P2",
            "mac": "P2",
            "os_linux": "P3",
            "linux": "P3",
            "alipaymini": "R2",
        },
        SSOENT_TO_APP={
            "A1": "web",
            "D1": "ios",
            "P1": "os_windows",
            "P2": "os_mac",
            "P3": "os_linux",
            "R2": "alipaymini",
        },
    )
    return SimpleNamespace(P115Client=FakeP115Client), const_module


class TestP115Service:
    """Tests for P115Service."""

    def test_list_login_devices_returns_whitelisted_standard_apps(
        self,
        p115_service,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """设备列表只暴露白名单内的可用标准端，失效与重复别名全部移除。"""
        fake_module, fake_const = build_fake_p115_module()
        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client_sync",
            lambda: (fake_module, fake_const),
        )

        items = p115_service.list_login_devices()
        values = [item.value for item in items]

        # 白名单顺序保留，且只保留 p115client 仍支持的端
        assert values == ["web", "ios", "os_windows", "os_mac", "os_linux", "alipaymini"]
        # desktop/bipad 实测失效，bios/bandroid/windows/mac/linux 与标准端重复
        assert not {
            "desktop",
            "bipad",
            "bios",
            "bandroid",
            "windows",
            "mac",
            "linux",
        } & set(values)
        assert {item.group for item in items} == {"standard"}
        assert all(item.label and item.label != item.value for item in items)

    @pytest.mark.asyncio
    async def test_get_status_uses_saved_115_config(self, config_service: ConfigService):
        """Status should be derived from saved 115 config."""
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        status = await service.get_status()

        assert status.enabled is True
        assert status.app == "alipaymini"
        assert status.is_logged_in is True

    @pytest.mark.asyncio
    async def test_get_status_defaults_to_alipaymini(self, config_service: ConfigService):
        """Status should default app to alipaymini when no config exists."""
        service = P115Service(config_service=config_service)

        status = await service.get_status()

        assert status.app == "alipaymini"
        assert status.enabled is False
        assert status.is_logged_in is False

    @pytest.mark.asyncio
    async def test_start_and_poll_qr_login_save_cookies(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Successful QR login should persist cookies back to ConfigService."""
        fake_module, fake_const = build_fake_p115_module()
        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        session = await service.start_qr_login("alipaymini")
        poll_status = await service.poll_qr_login(session.uid, session.app)
        saved_config = await config_service.get_115_config()

        assert session.uid == "uid-123"
        assert session.qrcode_url == "https://115.com/scan/dg-uid-123"
        assert poll_status.status == "success"
        assert poll_status.is_logged_in is True
        assert fake_module.P115Client.token_calls[-1]["async_"] is True
        assert fake_module.P115Client.status_calls[-1]["async_"] is True
        assert fake_module.P115Client.result_calls[-1]["async_"] is True
        assert saved_config.enabled is True
        assert saved_config.app == "alipaymini"
        assert saved_config.cookies == "UID=1; CID=2; SEID=3"
        assert saved_config.is_logged_in is True

    @pytest.mark.asyncio
    async def test_start_qr_login_passes_normalized_app_with_real_calling_convention(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """QR token generation should receive the normalized selected app."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        session = await service.start_qr_login("windows")

        assert session.app == "os_windows"
        assert fake_module.P115Client.token_calls == [
            {
                "self": None,
                "app": "os_windows",
                "base_url": "https://hnqrcodeapi.115.com",
                "async_": True,
            }
        ]

    @pytest.mark.asyncio
    async def test_poll_qr_login_treats_empty_long_poll_status_as_pending(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """长轮询未发生变化时返回空 data，应视为等待扫码而不是未知状态。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def empty_status(payload: dict) -> dict:
            assert payload["uid"] == "uid-123"
            return {"state": 1, "code": 0, "message": "", "data": {}}

        monkeypatch.setattr(
            fake_module.P115Client, "_status_response", staticmethod(empty_status)
        )

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        session = await service.start_qr_login("web")
        poll_status = await service.poll_qr_login(session.uid, session.app)

        assert poll_status.status == "pending"
        assert poll_status.message == "等待扫码"
        assert poll_status.is_logged_in is False
        # 未登录时不得写入 115 配置，且会话仍需保留以便下一次轮询
        assert fake_module.P115Client.result_calls == []
        assert (await config_service.get_115_config()).is_logged_in is False

    @pytest.mark.asyncio
    async def test_login_api_falls_back_to_next_host_on_failure(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """首个域名不可用（实测 405）时应自动改用下一个候选域名。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        original_status = fake_module.P115Client.login_qrcode_scan_status

        def flaky_status(payload: dict, base_url: str = "", async_: bool = False) -> dict:
            if base_url == "https://hnqrcodeapi.115.com":
                # 失败也要记下这一次尝试（真实实现会在抛出前发出请求）
                fake_module.P115Client.status_calls.append(
                    {"payload": dict(payload), "base_url": base_url, "async_": async_}
                )
                raise RuntimeError("HTTP Error 405: Method Not Allowed")
            return original_status(payload, base_url=base_url, async_=async_)

        monkeypatch.setattr(
            fake_module.P115Client, "login_qrcode_scan_status", staticmethod(flaky_status)
        )

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        session = await service.start_qr_login("alipaymini")
        poll_status = await service.poll_qr_login(session.uid, session.app)

        attempted = [call["base_url"] for call in fake_module.P115Client.status_calls]
        assert attempted == [
            "https://hnqrcodeapi.115.com",
            "https://qrcodeapi.115.com",
        ]
        assert poll_status.status == "success"
        assert poll_status.is_logged_in is True

    @pytest.mark.asyncio
    async def test_login_api_raises_when_all_hosts_fail(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """所有域名都失败时应抛错，不得把故障伪装成「等待扫码」。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []


        def broken_token(app: str = "web", base_url: str = "", async_: bool = False) -> dict:
            raise RuntimeError(f"HTTP Error 405: {base_url}")

        monkeypatch.setattr(
            fake_module.P115Client, "login_qrcode_token", staticmethod(broken_token)
        )

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(RuntimeError, match="405"):
            await service.start_qr_login("web")

    @pytest.mark.asyncio
    async def test_poll_qr_login_reports_expired_when_token_is_invalid(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """令牌过期（约 5 分钟）时 115 返回 key invalid，必须报过期而不是继续等待。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def invalid_key(payload: dict) -> dict:
            assert payload["uid"] == "uid-123"
            return {"state": 0, "code": 40199002, "message": "key invalid", "data": {}}

        monkeypatch.setattr(
            fake_module.P115Client, "_status_response", staticmethod(invalid_key)
        )

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        session = await service.start_qr_login("web")
        status = await service.poll_qr_login(session.uid, session.app)

        assert status.status == "expired"
        assert status.message == "二维码已过期，请重新生成"
        assert status.is_logged_in is False
        # 失效会话要就地清掉，用户点「重新生成二维码」时不会留下垃圾行
        assert await config_service.get(service._qr_payload_key(session.uid)) is None

    @pytest.mark.asyncio
    async def test_poll_qr_login_surfaces_unexpected_status_error(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """其他状态查询错误要透传 115 的原文，不能伪装成「等待扫码」。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def rate_limited(payload: dict) -> dict:
            return {"state": 0, "code": 40199001, "message": "too many requests", "data": {}}

        monkeypatch.setattr(
            fake_module.P115Client, "_status_response", staticmethod(rate_limited)
        )

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        session = await service.start_qr_login("web")
        status = await service.poll_qr_login(session.uid, session.app)

        assert status.status == "unknown"
        assert "too many requests" in status.message

    @pytest.mark.asyncio
    async def test_start_qr_login_prunes_previous_qr_sessions(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """新开扫码会话时应回收上一次遗留的二维码会话行。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        await config_service.set(
            service._qr_payload_key("uid-old"),
            '{"uid": "uid-old", "time": 1710000000, "sign": "s", "app": "web"}',
            encrypted=True,
        )

        session = await service.start_qr_login("web")

        assert await config_service.get(service._qr_payload_key("uid-old")) is None
        assert await config_service.get(service._qr_payload_key(session.uid)) is not None

    @pytest.mark.asyncio
    async def test_start_qr_login_persists_qr_payload_encrypted(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Pending QR payload should be stored encrypted instead of plaintext."""
        fake_module, fake_const = build_fake_p115_module()

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)

        session = await service.start_qr_login("windows")

        async with aiosqlite.connect(config_service.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT value, encrypted FROM config WHERE key = ?",
                (service._qr_payload_key(session.uid),),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["encrypted"] == 1
        assert row["value"] != (
            '{"uid": "uid-123", "time": 1710000000, "sign": "sign-123", '
            '"qrcode": "https://115.com/scan/dg-uid-123", "app": "os_windows"}'
        )
        assert "sign-123" not in row["value"]
        assert "os_windows" not in row["value"]

    @pytest.mark.asyncio
    async def test_poll_qr_login_works_with_fresh_service_instance(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Polling should still work after creating a fresh service instance."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []
        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        first_service = P115Service(config_service=config_service)
        session = await first_service.start_qr_login("alipaymini")

        second_service = P115Service(config_service=config_service)
        poll_status = await second_service.poll_qr_login(session.uid, session.app)

        assert poll_status.status == "success"
        assert poll_status.is_logged_in is True
        assert fake_module.P115Client.status_calls[-1]["payload"]["time"] == 1710000000
        assert fake_module.P115Client.status_calls[-1]["payload"]["sign"] == "sign-123"

    @pytest.mark.asyncio
    async def test_poll_qr_login_prefers_persisted_canonical_app_after_restart(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Polling after restart should keep using the persisted canonical app."""

        class WindowsQrFakeP115Client(FakeP115Client):
            def login_qrcode_scan_result(
                uid: str,
                app: str,
                cookies=None,
                base_url: str = "",
                async_: bool = False,
            ) -> dict:
                WindowsQrFakeP115Client.result_calls.append(
                    {
                        "uid": uid,
                        "app": app,
                        "cookies": cookies,
                        "base_url": base_url,
                        "async_": async_,
                    }
                )
                if async_:
                    return WindowsQrFakeP115Client._result_response(
                        uid,
                        app,
                        cookies=cookies,
                    )
                return {
                    "data": {
                        "status": 2,
                        "cookie": {
                            "UID": "1",
                            "CID": "2",
                            "SEID": "3",
                        },
                    }
                }

            @staticmethod
            async def _result_response(uid: str, app: str, cookies=None) -> dict:
                assert uid == "uid-123"
                assert app == "os_windows"
                return {
                    "data": {
                        "status": 2,
                        "cookie": {
                            "UID": "1",
                            "CID": "2",
                            "SEID": "3",
                        },
                    }
                }

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=WindowsQrFakeP115Client)
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        first_service = P115Service(config_service=config_service)
        session = await first_service.start_qr_login("windows")

        second_service = P115Service(config_service=config_service)
        poll_status = await second_service.poll_qr_login(session.uid, "alipaymini")
        saved_config = await config_service.get_115_config()

        assert session.app == "os_windows"
        assert poll_status.status == "success"
        assert poll_status.app == "os_windows"
        assert poll_status.is_logged_in is True
        assert fake_module.P115Client.result_calls[-1]["app"] == "os_windows"
        assert saved_config.app == "os_windows"
        assert saved_config.cookies == "UID=1; CID=2; SEID=3"

    @pytest.mark.asyncio
    async def test_poll_qr_login_does_not_report_success_without_cookies(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Missing cookies in scan result must not be treated as login success."""

        class NoCookieFakeP115Client(FakeP115Client):
            def login_qrcode_scan_result(
                uid: str,
                app: str,
                cookies=None,
                base_url: str = "",
                async_: bool = False,
            ) -> dict:
                FakeP115Client.result_calls.append(
                    {
                        "uid": uid,
                        "app": app,
                        "cookies": cookies,
                        "base_url": base_url,
                        "async_": async_,
                    }
                )
                if async_:
                    return NoCookieFakeP115Client._result_response(
                        uid,
                        app,
                        cookies=cookies,
                    )
                return {"data": {"status": 2}}

            @staticmethod
            async def _result_response(uid: str, app: str, cookies=None) -> dict:
                assert uid == "uid-123"
                assert app == "alipaymini"
                return {"data": {"status": 2}}

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=NoCookieFakeP115Client)
        fake_module.P115Client.token_calls = []
        fake_module.P115Client.status_calls = []
        fake_module.P115Client.result_calls = []

        async def fake_load():
            return fake_module, fake_const
        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

        service = P115Service(config_service=config_service)
        await service.start_qr_login(None)

        poll_status = await service.poll_qr_login("uid-123", "")
        saved_config = await config_service.get_115_config()

        assert poll_status.status != "success"
        assert poll_status.is_logged_in is False
        assert saved_config == Cloud115Config()

    @pytest.mark.asyncio
    async def test_poll_qr_login_returns_missing_session_without_persisted_payload(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Missing QR payload should short-circuit locally without remote polling."""

        async def fail_if_loader_called():
            raise AssertionError("p115client loader should not be called without QR payload")

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fail_if_loader_called,
        )

        service = P115Service(config_service=config_service)

        poll_status = await service.poll_qr_login("uid-missing", "alipaymini")

        assert poll_status.uid == "uid-missing"
        assert poll_status.app == "alipaymini"
        assert poll_status.status == "expired"
        assert poll_status.is_logged_in is False
        assert "会话" in poll_status.message

    @pytest.mark.asyncio
    async def test_browse_returns_provider_entries_for_root_directory(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browse should map 115 root entries into provider-aware rows."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.init_calls = []
        fake_module.P115Client.fs_files_calls = []
        fake_module.P115Client.fs_info_calls = []
        fake_module.P115Client.fs_dir_getid_calls = []
        fake_module.P115Client.fs_files_response = {
            "path": [{"cid": "0", "pid": "0", "name": ""}],
            "count": 2,
            "data": [
                {"cid": "100", "pid": "0", "n": "电影", "te": "1710000000"},
                {"fid": "200", "cid": "0", "n": "Movie.mkv", "s": "123", "te": "1710000001"},
            ],
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        result = await service.browse(path="/115网盘", file_id="0", page=2, page_size=10)
        expected_dir_mtime = datetime.fromtimestamp(1710000000).isoformat()
        expected_file_mtime = datetime.fromtimestamp(1710000001).isoformat()

        assert fake_module.P115Client.init_calls[-1] == {
            "cookies": "UID=1; CID=2; SEID=3; KID=4",
            "app": "alipaymini",
            "app_id": 0,
            "console_qrcode": False,
        }
        assert fake_module.P115Client.fs_files_calls == [
            {
                "payload": {"cid": "0", "offset": 10, "limit": 10, "show_dir": 1},
                "async_": True,
            }
        ]
        assert result["current_path"] == "/115网盘"
        assert result["parent_path"] is None
        assert result["total"] == 2
        assert result["entries"] == [
            {
                "name": "电影",
                "path": "/115网盘/电影",
                "is_dir": True,
                "provider": "115",
                "file_id": "100",
                "parent_id": "0",
                "size": None,
                "mtime": expected_dir_mtime,
            },
            {
                "name": "Movie.mkv",
                "path": "/115网盘/Movie.mkv",
                "is_dir": False,
                "provider": "115",
                "file_id": "200",
                "parent_id": "0",
                "size": 123,
                "mtime": expected_file_mtime,
            },
        ]

    @pytest.mark.asyncio
    async def test_browse_resolves_directory_id_from_virtual_path_when_file_id_missing(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Virtual 115 subpaths should be resolved to a directory id before browsing."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.fs_dir_getid_calls = []
        fake_module.P115Client.fs_files_calls = []
        fake_module.P115Client.fs_dir_getid_response = {"id": "100"}
        fake_module.P115Client.fs_files_response = {
            "path": [
                {"cid": "0", "pid": "0", "name": ""},
                {"cid": "100", "pid": "0", "name": "电影"},
            ],
            "count": 1,
            "data": [
                {"cid": "300", "pid": "100", "n": "动作片"},
            ],
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        result = await service.browse(path="/115网盘/电影", file_id=None, page=1, page_size=5)

        assert fake_module.P115Client.fs_dir_getid_calls == [
            {"payload": "/电影", "async_": True, "kwargs": {}}
        ]
        assert fake_module.P115Client.fs_files_calls == [
            {
                "payload": {"cid": "100", "offset": 0, "limit": 5, "show_dir": 1},
                "async_": True,
            }
        ]
        assert result["current_path"] == "/115网盘/电影"
        assert result["entries"][0]["path"] == "/115网盘/电影/动作片"

    @pytest.mark.asyncio
    async def test_browse_uses_fs_info_to_complete_subdirectory_paths(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browse should fall back to fs_info when fs_files lacks breadcrumb data."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.fs_files_calls = []
        fake_module.P115Client.fs_info_calls = []
        fake_module.P115Client.fs_files_response = {
            "count": 1,
            "data": [
                {"cid": "300", "pid": "100", "n": "动作片"},
            ],
        }
        fake_module.P115Client.fs_info_response = {
            "data": {
                "file_id": "100",
                "file_name": "电影",
                "sha1": "",
                "paths": [
                    {"file_id": "0", "file_name": ""},
                    {"file_id": "100", "file_name": "电影"},
                ],
            }
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        result = await service.browse(path="", file_id="100", page=1, page_size=5)

        assert fake_module.P115Client.fs_files_calls == [
            {
                "payload": {"cid": "100", "offset": 0, "limit": 5, "show_dir": 1},
                "async_": True,
            }
        ]
        assert fake_module.P115Client.fs_info_calls == [
            {"payload": {"file_id": "100"}, "async_": True}
        ]
        assert result["current_path"] == "/115网盘/电影"
        assert result["parent_path"] == "/115网盘"
        assert result["entries"] == [
            {
                "name": "动作片",
                "path": "/115网盘/电影/动作片",
                "is_dir": True,
                "provider": "115",
                "file_id": "300",
                "parent_id": "100",
                "size": None,
                "mtime": None,
            }
        ]

    @pytest.mark.asyncio
    async def test_browse_raises_configuration_error_when_not_logged_in(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Browse should fail with an application error when 115 cookies are unavailable."""

        async def fail_if_loader_called():
            raise AssertionError("p115client loader should not be called without cookies")

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fail_if_loader_called,
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="115"):
            await service.browse(path="/115网盘", file_id="0", page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_browse_converts_fs_files_errors_to_configuration_error(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Authentication failures should surface as a login-expired business error."""

        class FsFilesErrorFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_files_response(payload: dict) -> dict:
                raise RuntimeError("cookies expired")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=FsFilesErrorFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="重新扫码登录") as exc_info:
            await service.browse(path="/115网盘", file_id="0", page=1, page_size=20)

        assert exc_info.value.details["config_key"] == "cloud_115_config"

    @pytest.mark.asyncio
    async def test_browse_surfaces_http_405_as_rate_limit_hint(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """115 对高频请求返 405（HTML 错误页）时应提示限流，而非通用失败。"""

        class RateLimitedFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_files_response(payload: dict) -> dict:
                raise HTTPError(
                    "https://webapi.115.com/files",
                    405,
                    "Method Not Allowed",
                    None,
                    None,
                )

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=RateLimitedFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="405") as exc_info:
            await service.browse(path="/115网盘", file_id="0", page=1, page_size=20)

        assert "过于频繁" in str(exc_info.value)
        assert exc_info.value.details["config_key"] == "cloud_115_config"

    @pytest.mark.asyncio
    async def test_browse_reports_other_http_codes_with_reason(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """其他 HTTP 错误码带上码与原因，便于定位是哪一段坏掉。"""

        class BadGatewayFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_files_response(payload: dict) -> dict:
                raise HTTPError(
                    "https://webapi.115.com/files",
                    502,
                    "Bad Gateway",
                    None,
                    None,
                )

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=BadGatewayFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="502") as exc_info:
            await service.browse(path="/115网盘", file_id="0", page=1, page_size=20)

        assert "Bad Gateway" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_browse_converts_missing_directory_to_folder_not_found(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Directory lookup failures should not be misreported as login expiration."""

        class MissingDirectoryFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_dir_getid_response(payload: str | dict) -> dict:
                raise FileNotFoundError("directory missing")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=MissingDirectoryFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(FolderNotFoundError, match="电影"):
            await service.browse(path="/115网盘/电影", file_id=None, page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_browse_converts_invalid_directory_arguments_to_invalid_folder(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Invalid browse arguments should not be translated into relogin guidance."""

        class InvalidDirectoryFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_dir_getid_response(payload: str | dict) -> dict:
                raise ValueError("bad path")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=InvalidDirectoryFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(InvalidFolderError, match="路径"):
            await service.browse(path="/115网盘/电影", file_id=None, page=1, page_size=20)

    @pytest.mark.asyncio
    async def test_browse_converts_unknown_provider_errors_to_generic_configuration_error(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Unknown provider failures should surface as a generic browse error."""

        class UnknownBrowseErrorFakeP115Client(FakeP115Client):
            @staticmethod
            async def _fs_info_response(payload: dict) -> dict:
                raise RuntimeError("upstream unavailable")

        fake_module, fake_const = build_fake_p115_module()
        fake_module = SimpleNamespace(P115Client=UnknownBrowseErrorFakeP115Client)
        fake_module.P115Client.fs_files_response = {
            "count": 1,
            "data": [
                {"cid": "300", "pid": "100", "n": "动作片"},
            ],
        }

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="目录浏览失败") as exc_info:
            await service.browse(path="", file_id="100", page=1, page_size=20)

        assert exc_info.value.details["config_key"] == "cloud_115_config"

    @pytest.mark.asyncio
    async def test_scan_folder_recursively_collects_video_files(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """scan_folder should walk sub-directories and keep only video files."""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.fs_files_calls = []

        # Two pages on the root: first page full (2 items, page_size=100 -> treat <100 as last page).
        # We return one page for root, one for the nested dir, by switching on cid.
        root_response = {
            "path": [{"cid": "0", "pid": "0", "name": ""}],
            "count": 2,
            "data": [
                {"cid": "100", "pid": "0", "n": "剧集"},
                {"fid": "200", "cid": "0", "n": "readme.txt", "s": "10", "te": "1710000000"},
            ],
        }
        nested_response = {
            "path": [
                {"cid": "0", "pid": "0", "name": ""},
                {"cid": "100", "pid": "0", "name": "剧集"},
            ],
            "count": 2,
            "data": [
                {"fid": "300", "cid": "100", "n": "S01E01.mkv", "s": "12345", "te": "1710000001"},
                {"fid": "301", "cid": "100", "n": "S01E02.mp4", "s": "12346", "te": "1710000002"},
            ],
        }

        class ScanFakeP115Client(FakeP115Client):
            def fs_files(self, payload: dict, async_: bool = False) -> dict:
                FakeP115Client.fs_files_calls.append({"payload": dict(payload), "async_": async_})
                cid = payload.get("cid")
                response = nested_response if cid == "100" else root_response
                if async_:
                    return type(self)._static_response(response)
                return response

            @staticmethod
            async def _static_response(response):
                return response

        fake_module = SimpleNamespace(P115Client=ScanFakeP115Client)

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="alipaymini",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

        service = P115Service(config_service=config_service)
        entries = await service.scan_folder(path="/115网盘", file_id="0")

        # Only video files kept; readme.txt filtered out.
        names = [e["name"] for e in entries]
        assert names == ["S01E01.mkv", "S01E02.mp4"]
        # Each entry carries enough to rebuild a StorageLocator.
        for entry in entries:
            assert entry["provider"] == "115"
            assert entry["is_dir"] is False
            assert entry["file_id"] in {"300", "301"}
            assert entry["parent_id"] == "100"
            assert entry["path"].startswith("/115网盘/剧集/")

    @pytest.mark.asyncio
    async def test_scan_folder_raises_configuration_error_when_not_logged_in(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """scan_folder should refuse when 115 cookies are unavailable."""

        async def fail_if_loader_called():
            raise AssertionError("loader should not be called without cookies")

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fail_if_loader_called,
        )

        service = P115Service(config_service=config_service)

        with pytest.raises(ConfigurationError, match="请先登录"):
            await service.scan_folder(path="/115网盘", file_id="0")

    def test_load_p115client_sync_restores_home_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Real p115client loader should restore HOME/USERPROFILE after import."""
        original_cache = p115_service_module._P115_MODULE_CACHE
        monkeypatch.setenv("HOME", "C:/tmp/test-home")
        monkeypatch.setenv("USERPROFILE", "C:/tmp/test-userprofile")
        p115_service_module._P115_MODULE_CACHE = None

        try:
            p115_module, const_module = p115_service_module._load_p115client_sync()
        finally:
            p115_service_module._P115_MODULE_CACHE = original_cache

        assert getattr(p115_module, "__name__", "") == "p115client"
        assert getattr(const_module, "__name__", "") == "p115client.const"
        assert os.environ["HOME"] == "C:/tmp/test-home"
        assert os.environ["USERPROFILE"] == "C:/tmp/test-userprofile"

    @pytest.mark.asyncio
    async def test_build_client_against_installed_pinned_library(
        self,
        config_service: ConfigService,
    ):
        """真实库构造客户端必须成功（回归：登录后浏览 115 一律 500）。

        2026-09-26：p115client pinned 到 0.0.9.6.5.1 后 ``__init__`` 只剩
        cookies/app/app_id/console_qrcode，代码里继续传 check_for_relogin /
        ensure_cookies 会抛 TypeError，表现为「扫码登录成功、一浏览 115 就 500」。
        假客户端收下多余关键字不会报错，因此这里不注入假模块，直接拿真实库构造
        （只解析 cookies，不发网络请求），才能挡住签名漂移。
        """
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="harmony",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )
        service = P115Service(config_service=config_service)
        config = await config_service.get_115_config()
        p115_module, _ = p115_service_module._load_p115client_sync()

        client = await service._load_p115_client_with_config(config)

        assert isinstance(client, p115_module.P115Client)
        assert client.app_id == 0
        assert "UID=1" in str(client.cookies)

    def test_removed_legacy_kwargs_are_rejected_by_pinned_library(self):
        """pinned 库不再接受旧参数——这正是登录后 500 的直接原因。"""
        p115_module, _ = p115_service_module._load_p115client_sync()

        with pytest.raises(TypeError):
            p115_module.P115Client(
                "UID=1; CID=2; SEID=3; KID=4",
                check_for_relogin=False,
                ensure_cookies=False,
                app="alipaymini",
                console_qrcode=False,
            )

    def test_container_get_service_resolves_p115_with_config_dependency(
        self,
        config_service: ConfigService,
    ):
        """Container runtime path should resolve P115Service without crashing."""
        container = get_container()
        container.clear()
        try:
            container.register_instance(Services.CONFIG, config_service)

            service = get_service(Services.P115)

            assert isinstance(service, P115Service)
            assert service.config_service is config_service
        finally:
            container.clear()


class TestP115AccountInfo:
    """账号详情（身份 / 会员 / 容量 / 当前设备）与登录态探测。"""

    @staticmethod
    def _patch_client(monkeypatch: pytest.MonkeyPatch) -> None:
        """把真实 p115client 换成 FakeP115Client，并清掉上一条用例的调用记录。"""
        fake_module, fake_const = build_fake_p115_module()
        fake_module.P115Client.user_base_info_calls = []
        fake_module.P115Client.login_devices_calls = []
        fake_module.P115Client.user_base_info_error = None
        fake_module.P115Client.login_devices_error = None

        async def fake_load():
            return fake_module, fake_const

        monkeypatch.setattr(
            "server.domain.integration.p115_service._load_p115client",
            fake_load,
        )

    @staticmethod
    async def _login(config_service: ConfigService) -> None:
        await config_service.save_115_config(
            Cloud115Config(
                enabled=True,
                app="harmony",
                cookies="UID=1; CID=2; SEID=3; KID=4",
                is_logged_in=True,
            )
        )

    @pytest.mark.asyncio
    async def test_returns_not_logged_in_without_saved_config(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """没登录时不该打 115，也不该把登录态说成「已失效」。"""
        self._patch_client(monkeypatch)
        service = P115Service(config_service=config_service)

        info = await service.get_account_info()

        assert info.is_logged_in is False
        assert info.is_session_valid is None
        assert info.message == ""
        assert info.account is None
        assert info.storage is None
        assert FakeP115Client.user_base_info_calls == []

    @pytest.mark.asyncio
    async def test_maps_identity_membership_storage_and_device(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """真实响应形状（2026-09-26 实测抄录）应逐字段落到账号详情。"""
        self._patch_client(monkeypatch)
        await self._login(config_service)
        service = P115Service(config_service=config_service)

        info = await service.get_account_info()

        assert info.is_logged_in is True
        assert info.is_session_valid is True
        assert info.account is not None
        assert info.account.user_id == "336320871"
        assert info.account.nickname == "测试账号"
        assert info.account.avatar_url == "https://avatars.115.com/01/xxx_l.jpg"
        assert info.account.device_count == 8
        assert info.membership is not None
        assert info.membership.is_vip is True
        assert info.membership.level_name == "年费VIP"
        assert info.membership.expire_date == "2028-07-01"
        assert info.storage is not None
        assert info.storage.used_bytes == 37474186538573
        assert info.storage.total_bytes == 75136879795860
        assert info.storage.used_text == "34.08TB"
        assert info.storage.total_text == "68.34TB"
        assert info.storage.used_percent == pytest.approx(49.87, abs=0.01)
        assert info.device is not None
        assert info.device.name == "115_鸿蒙端"
        assert info.device.city == "江苏"
        assert info.device.ip == "223.64.***.39"
        assert info.device.login_at is not None
        assert info.device.is_unusual is False
        # 账号接口只调一次，且用登录时选的设备端（harmony）
        assert FakeP115Client.user_base_info_calls[-1]["app"] == "harmony"
        assert FakeP115Client.user_base_info_calls[-1]["async_"] is True

    @pytest.mark.asyncio
    async def test_reports_invalid_session_when_115_asks_to_relogin(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """cookie 失效时 115 返回 state=0 + 40101032（实测），要判成登录已失效。"""
        self._patch_client(monkeypatch)
        await self._login(config_service)
        original_response = FakeP115Client.user_base_info_response
        FakeP115Client.user_base_info_response = {
            "state": 0,
            "code": 40101032,
            "data": {},
            "message": "请重新登录",
            "error": "请重新登录",
            "errno": 40101032,
        }
        try:
            service = P115Service(config_service=config_service)
            info = await service.get_account_info()
        finally:
            # 类属性会被后续用例共享，必须还原
            FakeP115Client.user_base_info_response = original_response
        assert info.is_logged_in is True
        assert info.is_session_valid is False
        assert info.message == "115 登录已失效，请重新扫码登录"

    @pytest.mark.asyncio
    async def test_degrades_to_unknown_when_probe_raises(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """网络/解析失败不能冒充「已失效」，也不能冒充「有效」。"""
        self._patch_client(monkeypatch)
        await self._login(config_service)
        FakeP115Client.user_base_info_error = TimeoutError("timed out")
        service = P115Service(config_service=config_service)

        info = await service.get_account_info()

        assert info.is_logged_in is True
        assert info.is_session_valid is None
        assert info.message == "115 账号信息读取失败，请稍后重试"
        assert info.account is None

    @pytest.mark.asyncio
    async def test_keeps_identity_when_device_list_fails(
        self,
        config_service: ConfigService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """设备列表只是增强项：它失败时账号卡仍要完整。"""
        self._patch_client(monkeypatch)
        await self._login(config_service)
        FakeP115Client.login_devices_error = RuntimeError("device list boom")
        service = P115Service(config_service=config_service)

        info = await service.get_account_info()

        assert info.is_session_valid is True
        assert info.account is not None
        assert info.storage is not None
        assert info.device is None
