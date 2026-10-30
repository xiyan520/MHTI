"""Unit tests for EmbyService.

测试 EmbyService 的核心功能：
- 配置状态检查
- 连接测试
- 冲突检测
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from server.domain.integration.emby_service import EmbyService
from server.domain.system.config_service import ConfigService
from server.models.emby import EmbyConfig, EmbyStatus, EmbyTestResponse


@pytest.fixture
def config_service(temp_db: Path) -> ConfigService:
    """提供使用临时数据库的 ConfigService。"""
    return ConfigService(db_path=temp_db)


@pytest.fixture
def emby_service(config_service: ConfigService) -> EmbyService:
    """提供 EmbyService 实例。"""
    return EmbyService(config_service=config_service)


class TestEmbyStatus:
    """测试 Emby 状态检查。"""

    @pytest.mark.asyncio
    async def test_get_status_not_configured(self, emby_service):
        """测试未配置时返回 is_configured=False。"""
        # 默认配置应该是未启用的
        status = await emby_service.get_status()

        assert isinstance(status, EmbyStatus)
        assert status.is_configured is False

    @pytest.mark.asyncio
    async def test_get_status_configured(self, emby_service, config_service):
        """测试已配置时返回 is_configured=True。"""
        # 保存一个启用的配置
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="test_api_key",
        )
        await config_service.save_emby_config(config)

        status = await emby_service.get_status()

        assert status.is_configured is True


class TestEmbyConnectionTest:
    """测试 Emby 连接测试功能。"""

    @pytest.mark.asyncio
    async def test_test_connection_not_configured(self, emby_service):
        """测试未配置时连接测试失败。"""
        result = await emby_service.test_connection()

        assert isinstance(result, EmbyTestResponse)
        assert result.success is False
        assert "配置" in result.message

    @pytest.mark.asyncio
    async def test_test_connection_with_empty_url(self, emby_service):
        """测试空 URL 时连接测试失败。"""
        config = EmbyConfig(
            enabled=True,
            server_url="",
            api_key="test_key",
        )

        result = await emby_service.test_connection_with_config(config)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_test_connection_with_empty_api_key(self, emby_service):
        """测试空 API Key 时连接测试失败。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="",
        )

        result = await emby_service.test_connection_with_config(config)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_test_connection_timeout(self, emby_service):
        """测试连接超时。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="test_key",
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock
        ) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("timeout")

            result = await emby_service.test_connection_with_config(config)

            assert result.success is False
            assert "超时" in result.message

    @pytest.mark.asyncio
    async def test_test_connection_request_error(self, emby_service):
        """测试连接请求错误。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="test_key",
        )

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock
        ) as mock_get:
            mock_get.side_effect = httpx.RequestError("connection refused")

            result = await emby_service.test_connection_with_config(config)

            assert result.success is False
            assert "连接失败" in result.message

    @pytest.mark.asyncio
    async def test_test_connection_success(self, emby_service):
        """测试连接成功。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="test_key",
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ServerName": "Test Emby Server",
            "Version": "4.7.0.0",
        }
        mock_response.raise_for_status = MagicMock()

        with patch.object(
            httpx.AsyncClient, "get", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_response

            with patch.object(
                emby_service, "_get_libraries", new_callable=AsyncMock
            ) as mock_libs:
                mock_libs.return_value = []

                result = await emby_service.test_connection_with_config(config)

                assert result.success is True
                assert result.server_name == "Test Emby Server"
                assert result.server_version == "4.7.0.0"


class TestEmbyGetClient:
    """测试 HTTP 客户端创建。"""

    def test_get_client_headers(self, emby_service):
        """测试客户端请求头正确设置。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="my_api_key_123",
        )

        client = emby_service._get_client(config)

        assert "X-Emby-Token" in client.headers
        assert client.headers["X-Emby-Token"] == "my_api_key_123"
        assert client.headers["Accept"] == "application/json"

    def test_get_client_base_url(self, emby_service):
        """测试客户端基础 URL 正确设置。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096/",  # 带尾部斜杠
            api_key="test_key",
        )

        client = emby_service._get_client(config)

        # 应该去掉尾部斜杠
        assert str(client.base_url) == "http://localhost:8096"

    def test_get_client_timeout(self, emby_service):
        """测试客户端超时正确设置。"""
        config = EmbyConfig(
            enabled=True,
            server_url="http://localhost:8096",
            api_key="test_key",
            timeout=30.0,
        )

        client = emby_service._get_client(config)

        assert client.timeout.connect == 30.0


class TestEmbyGetLibraries:
    """测试获取媒体库列表功能。"""

    @pytest.mark.asyncio
    async def test_get_libraries_with_user_id(self, emby_service):
        """测试有 user_id 时使用 /Users/{user_id}/Views API。"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "Items": [
                {
                    "Id": "lib1",
                    "Name": "电视剧",
                    "CollectionType": "tvshows",
                    "ChildCount": 42,
                },
                {
                    "Id": "lib2",
                    "Name": "电影",
                    "CollectionType": "movies",
                    "ChildCount": 100,
                },
                {
                    "Id": "lib3",
                    "Name": "音乐",
                    "CollectionType": "music",  # 不支持的类型
                    "ChildCount": 50,
                },
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        libraries = await emby_service._get_libraries(mock_client, user_id="user123")

        # 验证调用了正确的 API
        mock_client.get.assert_called_once_with("/Users/user123/Views")

        # 验证只返回支持的媒体库类型
        assert len(libraries) == 2
        assert libraries[0].id == "lib1"
        assert libraries[0].name == "电视剧"
        assert libraries[0].type == "tvshows"
        assert libraries[0].item_count == 42
        assert libraries[1].id == "lib2"
        assert libraries[1].item_count == 100

    @pytest.mark.asyncio
    async def test_get_libraries_without_user_id(self, emby_service):
        """测试无 user_id 时使用 /Library/VirtualFolders API 并额外获取项目数。"""
        # VirtualFolders API 响应（不包含 ChildCount）
        folders_response = MagicMock()
        folders_response.status_code = 200
        folders_response.json.return_value = [
            {
                "ItemId": "lib1",
                "Name": "电视剧",
                "CollectionType": "tvshows",
            },
            {
                "ItemId": "lib2",
                "Name": "电影",
                "CollectionType": "movies",
            },
        ]
        folders_response.raise_for_status = MagicMock()

        # Items API 响应（获取项目数）
        items_response_lib1 = MagicMock()
        items_response_lib1.status_code = 200
        items_response_lib1.json.return_value = {"TotalRecordCount": 42}

        items_response_lib2 = MagicMock()
        items_response_lib2.status_code = 200
        items_response_lib2.json.return_value = {"TotalRecordCount": 100}

        mock_client = AsyncMock()

        async def mock_get(url, params=None):
            if url == "/Library/VirtualFolders":
                return folders_response
            elif url == "/Items" and params and params.get("ParentId") == "lib1":
                return items_response_lib1
            elif url == "/Items" and params and params.get("ParentId") == "lib2":
                return items_response_lib2
            return MagicMock(status_code=404)

        mock_client.get = mock_get

        libraries = await emby_service._get_libraries(mock_client, user_id="")

        # 验证返回正确的媒体库信息
        assert len(libraries) == 2
        assert libraries[0].id == "lib1"
        assert libraries[0].name == "电视剧"
        assert libraries[0].type == "tvshows"
        assert libraries[0].item_count == 42  # 通过 /Items API 获取
        assert libraries[1].id == "lib2"
        assert libraries[1].item_count == 100

    @pytest.mark.asyncio
    async def test_get_libraries_error_handling(self, emby_service):
        """测试获取媒体库列表失败时返回空列表。"""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        libraries = await emby_service._get_libraries(mock_client, user_id="")

        assert libraries == []
