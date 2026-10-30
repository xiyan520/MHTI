"""Unit tests for config API endpoints.

测试 /api/config 路由。
使用 conftest.py 中的 override_auth fixture 绕过认证。
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

from server.bootstrap import get_config_service, get_p115_service, get_tmdb_service
from server.domain.integration.p115_service import P115Service
from server.domain.metadata.tmdb_service import TMDBService
from server.domain.system.config_service import ConfigService
from server.main import app
from server.models.cloud_115 import (
    Cloud115Account,
    Cloud115AccountInfo,
    Cloud115Config,
    Cloud115DeviceOption,
    Cloud115Membership,
    Cloud115QrSession,
    Cloud115QrStatus,
    Cloud115Status,
    Cloud115Storage,
)
from server.models.config import ApiTokenStatus


@pytest.fixture
def config_client(temp_db: Path, override_auth) -> TestClient:
    """
    提供带有配置服务和认证覆盖的测试客户端。

    Args:
        temp_db: Temporary database path.
        override_auth: Authentication override fixture.

    Returns:
        Configured test client.
    """
    config_service = ConfigService(db_path=temp_db)
    tmdb_service = TMDBService(config_service=config_service)
    p115_service = Mock(spec=P115Service)
    p115_service.get_status = AsyncMock(
        return_value=Cloud115Status(
            enabled=True,
            app="alipaymini",
            is_logged_in=True,
        )
    )
    p115_service.list_login_devices = Mock(
        return_value=[
            Cloud115DeviceOption(value="web", label="115生活_网页端", group="standard"),
            Cloud115DeviceOption(value="alipaymini", label="115生活_支付宝小程序", group="standard"),
        ]
    )
    p115_service.start_qr_login = AsyncMock(
        return_value=Cloud115QrSession(
            uid="uid-123",
            qrcode_url="https://115.com/scan/dg-uid-123",
            app="alipaymini",
        )
    )
    p115_service.poll_qr_login = AsyncMock(
        return_value=Cloud115QrStatus(
            uid="uid-123",
            app="alipaymini",
            status="success",
            message="登录成功",
            is_logged_in=True,
        )
    )
    p115_service.clear_login_state = AsyncMock()
    p115_service.get_account_info = AsyncMock(
        return_value=Cloud115AccountInfo(
            is_logged_in=True,
            is_session_valid=True,
            account=Cloud115Account(
                user_id="336320871",
                nickname="测试账号",
                avatar_url="https://avatars.115.com/01/xxx_l.jpg",
                device_count=8,
            ),
            membership=Cloud115Membership(
                is_vip=True,
                level_name="年费VIP",
                expire_date="2028-07-01",
            ),
            storage=Cloud115Storage(
                used_bytes=37474186538573,
                total_bytes=75136879795860,
                used_text="34.08TB",
                total_text="68.34TB",
                used_percent=49.87,
            ),
        )
    )

    def override_config_service():
        return config_service

    def override_tmdb_service():
        return tmdb_service

    def override_p115_service():
        return p115_service

    app.dependency_overrides[get_config_service] = override_config_service
    app.dependency_overrides[get_tmdb_service] = override_tmdb_service
    app.dependency_overrides[get_p115_service] = override_p115_service

    client = TestClient(app)
    client.config_service = config_service
    client.p115_service = p115_service
    yield client
    app.dependency_overrides.clear()


class TestProxyAPI:
    """Tests for /api/config/proxy endpoints."""

    def test_get_proxy_default(self, config_client):
        """Test getting default proxy configuration."""
        response = config_client.get("/api/config/proxy")

        assert response.status_code == 200
        data = response.json()
        assert "type" in data
        assert "host" in data
        assert "port" in data

    def test_save_proxy_config(self, config_client):
        """Test saving proxy configuration via PUT."""
        response = config_client.put(
            "/api/config/proxy",
            json={
                "type": "http",
                "host": "127.0.0.1",
                "port": 7890,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "http"
        assert data["host"] == "127.0.0.1"
        assert data["port"] == 7890

    def test_save_proxy_with_auth(self, config_client):
        """Test saving proxy configuration with authentication."""
        response = config_client.put(
            "/api/config/proxy",
            json={
                "type": "socks5",
                "host": "proxy.example.com",
                "port": 1080,
                "username": "user",
                "password": "pass",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["has_auth"] is True

    def test_delete_proxy_config(self, config_client):
        """Test deleting proxy configuration."""
        # First save a proxy
        config_client.put(
            "/api/config/proxy",
            json={"type": "http", "host": "127.0.0.1", "port": 7890},
        )

        # Then delete it
        response = config_client.delete("/api/config/proxy")

        assert response.status_code == 200

    def test_proxy_test_mocked_success(self, config_client):
        """Test proxy connection testing with mocked success."""
        with patch.object(
            TMDBService, "test_proxy", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = (True, "连接成功", 150)

            response = config_client.post("/api/config/proxy/test")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["latency_ms"] == 150

    def test_proxy_test_mocked_failure(self, config_client):
        """Test proxy connection testing with mocked failure."""
        with patch.object(
            TMDBService, "test_proxy", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = (False, "连接超时", None)

            response = config_client.post("/api/config/proxy/test")

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False

    def test_proxy_test_uses_request_config(self, config_client):
        """Test proxy connection uses the current request config instead of only saved config."""
        with patch.object(
            TMDBService, "test_proxy", new_callable=AsyncMock
        ) as mock_test:
            mock_test.return_value = (True, "连接成功", 88)

            response = config_client.post(
                "/api/config/proxy/test",
                json={
                    "type": "http",
                    "host": "127.0.0.1",
                    "port": 7890,
                },
            )

            assert response.status_code == 200
            mock_test.assert_awaited_once_with("http://127.0.0.1:7890")


class TestAPITokenAPI:
    """Tests for /api/config/api-token endpoints."""

    def test_get_api_token_status_not_configured(self, config_client):
        """Test getting status when no API token is configured."""
        response = config_client.get("/api/config/api-token/status")

        assert response.status_code == 200
        data = response.json()
        assert data["is_configured"] is False

    def test_save_api_token_mocked_success(self, config_client):
        """Test saving API token with mocked verification.

        保存现在会顺带探测 R18（打 TMDB），测试同样把它换掉：否则用例成败取决于
        当前环境能不能联上 TMDB，且会挂到超时。
        """
        with (
            patch.object(
                TMDBService, "verify_api_token", new_callable=AsyncMock
            ) as mock_verify,
            patch.object(
                TMDBService, "check_adult_access", new_callable=AsyncMock
            ) as mock_adult,
        ):
            mock_verify.return_value = (True, None)
            mock_adult.return_value = (True, "已开启（关键词「hentai」命中成人内容）")

            response = config_client.post(
                "/api/config/api-token",
                json={"token": "valid_api_token_12345"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["status"]["adult_enabled"] is True

    def test_save_api_token_invalid(self, config_client):
        """Test saving API token with mocked failed verification."""
        with patch.object(
            TMDBService, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (False, "Invalid API token")

            response = config_client.post(
                "/api/config/api-token",
                json={"token": "invalid_token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            # 验证失败时不探测 R18（避免用无效凭据白打 TMDB）
            assert data["status"]["adult_enabled"] is None

    def test_save_api_token_empty(self, config_client):
        """Test saving empty API token."""
        response = config_client.post(
            "/api/config/api-token",
            json={"token": ""},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_delete_api_token_not_configured(self, config_client):
        """Test deleting when no API token is configured."""
        response = config_client.delete("/api/config/api-token")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False

    def test_api_token_missing_field(self, config_client):
        """Test 422 response for missing token field."""
        response = config_client.post("/api/config/api-token", json={})

        assert response.status_code == 422

    def test_verify_endpoint_returns_refreshed_status(self, config_client):
        """POST /api-token/verify 走远端重验 + 重探 R18。"""
        with patch.object(
            TMDBService, "refresh_token_status", new_callable=AsyncMock
        ) as mock_refresh:
            mock_refresh.return_value = ApiTokenStatus(
                is_configured=True,
                is_valid=True,
                adult_enabled=False,
                adult_message="未开启：TMDB 账户设置里隐藏了成人内容，刮削会找不到任何结果",
                adult_checked_at=datetime(2026, 9, 26, 15, 2),
            )

            response = config_client.post("/api/config/api-token/verify")

        assert response.status_code == 200
        data = response.json()
        assert data["is_valid"] is True
        assert data["adult_enabled"] is False
        assert "未开启" in data["adult_message"]
        assert data["adult_checked_at"] is not None

    def test_verify_endpoint_without_token(self, config_client):
        """未配置 Token 时返回未配置（不报错）。"""
        response = config_client.post("/api/config/api-token/verify")

        assert response.status_code == 200
        assert response.json()["is_configured"] is False


class TestLanguageAPI:
    """Tests for /api/config/language endpoints."""

    def test_get_language_default(self, config_client):
        """Test getting default language configuration."""
        response = config_client.get("/api/config/language")

        assert response.status_code == 200
        data = response.json()
        assert "primary" in data

    def test_save_language_config(self, config_client):
        """Test saving language configuration."""
        response = config_client.put(
            "/api/config/language",
            json={"primary": "en-US"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["primary"] == "en-US"


class TestOrganizeAPI:
    """Tests for /api/config/organize endpoints."""

    def test_get_organize_default(self, config_client):
        """Test getting default organize configuration."""
        response = config_client.get("/api/config/organize")

        assert response.status_code == 200
        data = response.json()
        # 检查实际存在的字段
        assert "organize_mode" in data or "organize_dir" in data


class TestNamingAPI:
    """Tests for /api/config/naming endpoints."""

    def test_get_naming_default(self, config_client):
        """Test getting default naming configuration."""
        response = config_client.get("/api/config/naming")

        assert response.status_code == 200
        data = response.json()
        assert data["series_folder"] == "{title} ({year})"
        assert data["season_folder"] == "Season {season}"

    def test_save_naming_config(self, config_client):
        """Test saving naming configuration."""
        payload = {
            "series_folder": "{title}",
            "season_folder": "S{season:02d}",
            "episode_file": "{title}.S{season:02d}E{episode:02d}",
        }

        save_response = config_client.put("/api/config/naming", json=payload)
        get_response = config_client.get("/api/config/naming")

        assert save_response.status_code == 200
        assert save_response.json() == payload
        assert get_response.status_code == 200
        assert get_response.json() == payload


class TestNfoAPI:
    """Tests for /api/config/nfo endpoints."""

    def test_get_nfo_default(self, config_client):
        """Test getting default NFO configuration."""
        response = config_client.get("/api/config/nfo")

        assert response.status_code == 200


class TestSystemAPI:
    """Tests for /api/config/system endpoints."""

    def test_get_system_default(self, config_client):
        """Test getting default system configuration."""
        response = config_client.get("/api/config/system")

        assert response.status_code == 200


class TestCloud115API:
    """Tests for /api/config/115 endpoints."""

    def test_get_115_status(self, config_client):
        """Test getting current 115 login status."""
        response = config_client.get("/api/config/115")

        assert response.status_code == 200
        assert response.json() == {
            "enabled": True,
            "app": "alipaymini",
            "is_logged_in": True,
            "updated_at": None,
        }
        config_client.p115_service.get_status.assert_awaited_once()

    def test_get_115_account_info(self, config_client):
        """Test getting 115 account details (identity / membership / storage)."""
        response = config_client.get("/api/config/115/account")

        assert response.status_code == 200
        payload = response.json()
        assert payload["is_session_valid"] is True
        assert payload["account"]["nickname"] == "测试账号"
        assert payload["account"]["user_id"] == "336320871"
        assert payload["membership"]["level_name"] == "年费VIP"
        assert payload["storage"]["used_text"] == "34.08TB"
        assert payload["storage"]["used_percent"] == 49.87
        config_client.p115_service.get_account_info.assert_awaited_once()

    def test_get_115_devices(self, config_client):
        """Test listing supported 115 login devices."""
        response = config_client.get("/api/config/115/devices")

        assert response.status_code == 200
        data = response.json()
        assert [item["value"] for item in data["items"]] == ["web", "alipaymini"]
        assert {item["group"] for item in data["items"]} == {"standard"}
        config_client.p115_service.list_login_devices.assert_called_once_with()

    def test_start_115_qrcode_login(self, config_client):
        """Test creating a QR login session."""
        response = config_client.post(
            "/api/config/115/login/qrcode",
            json={"app": "alipaymini"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "uid": "uid-123",
            "qrcode_url": "https://115.com/scan/dg-uid-123",
            "app": "alipaymini",
        }
        config_client.p115_service.start_qr_login.assert_awaited_once_with("alipaymini")

    def test_get_115_qrcode_login_status(self, config_client):
        """Test polling a QR login session status."""
        response = config_client.get(
            "/api/config/115/login/status",
            params={"uid": "uid-123", "app": "alipaymini"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "uid": "uid-123",
            "app": "alipaymini",
            "status": "success",
            "message": "登录成功",
            "is_logged_in": True,
        }
        config_client.p115_service.poll_qr_login.assert_awaited_once_with(
            "uid-123",
            "alipaymini",
        )

    def test_delete_115_login(self, config_client):
        """Test clearing stored 115 login information."""
        real_p115_service = P115Service(config_service=config_client.config_service)
        app.dependency_overrides[get_p115_service] = lambda: real_p115_service
        payload_key = real_p115_service._qr_payload_key("uid-123")

        asyncio.run(
            config_client.config_service.save_115_config(
                Cloud115Config(
                    enabled=True,
                    app="alipaymini",
                    cookies="UID=1; CID=2; SEID=3",
                    is_logged_in=True,
                )
            )
        )
        asyncio.run(
            config_client.config_service.set(
                payload_key,
                json.dumps(
                    {
                        "uid": "uid-123",
                        "time": 1710000000,
                        "sign": "sign-123",
                        "app": "os_windows",
                    }
                ),
            )
        )

        response = config_client.delete("/api/config/115/login")
        saved_config = asyncio.run(config_client.config_service.get_115_config())
        saved_payload = asyncio.run(config_client.config_service.get(payload_key))

        assert response.status_code == 200
        assert response.json() == {"success": True, "message": "115 登录信息已清除"}
        assert saved_config == Cloud115Config()
        assert saved_payload is None
