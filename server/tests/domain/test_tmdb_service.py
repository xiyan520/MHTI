"""Unit tests for TMDBService."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.common.exceptions import TMDBTimeoutError
from server.domain.metadata.tmdb_service import TMDBService
from server.domain.system.config_service import ConfigService


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


@pytest.fixture
def config_service(temp_db):
    """Provide a ConfigService instance with temp database."""
    return ConfigService(db_path=temp_db)


@pytest.fixture
def tmdb_service(config_service):
    """Provide a TMDBService instance."""
    return TMDBService(config_service=config_service)


class TestTMDBService:
    """Tests for TMDBService class."""

    def test_get_image_url(self, tmdb_service):
        """Test image URL generation."""
        url = tmdb_service.get_image_url("/abc123.jpg", "w500")
        assert url == "https://image.tmdb.org/t/p/w500/abc123.jpg"

    def test_get_image_url_none(self, tmdb_service):
        """Test image URL with None path."""
        url = tmdb_service.get_image_url(None)
        assert url is None

    def test_get_image_url_original(self, tmdb_service):
        """Test image URL with original size."""
        url = tmdb_service.get_image_url("/poster.jpg", "original")
        assert url == "https://image.tmdb.org/t/p/original/poster.jpg"

    def test_parse_date(self, tmdb_service):
        """Test date parsing."""
        from datetime import date as dt_date

        result = tmdb_service._parse_date("2024-01-15")
        assert result == dt_date(2024, 1, 15)

    def test_parse_date_invalid(self, tmdb_service):
        """Test invalid date parsing."""
        result = tmdb_service._parse_date("invalid")
        assert result is None

    def test_parse_date_none(self, tmdb_service):
        """Test None date parsing."""
        result = tmdb_service._parse_date(None)
        assert result is None

    def test_is_bearer_token(self, tmdb_service):
        """Test bearer token detection."""
        assert tmdb_service._is_bearer_token("eyJhbGciOiJIUzI1NiJ9.xxx") is True
        assert tmdb_service._is_bearer_token("abc123apikey") is False


class TestTMDBServiceAPIToken:
    """Tests for TMDBService API token methods."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    @pytest.fixture
    def tmdb_service(self, temp_db):
        """Provide a TMDBService instance."""
        config_service = ConfigService(db_path=temp_db)
        return TMDBService(config_service=config_service)

    @pytest.mark.asyncio
    async def test_get_api_token_status_not_configured(self, tmdb_service):
        """Test status when no API token is configured."""
        status = await tmdb_service.get_api_token_status()
        assert status.is_configured is False

    @pytest.mark.asyncio
    async def test_save_and_verify_empty_token(self, tmdb_service):
        """Test save with empty token."""
        status = await tmdb_service.save_and_verify_api_token("")
        assert status.is_configured is False
        assert status.is_valid is False
        assert status.error_message is not None

    @pytest.mark.asyncio
    async def test_save_and_verify_mocked_success(self, tmdb_service):
        """Test save with mocked successful verification."""
        with patch.object(
            tmdb_service, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (True, None)

            status = await tmdb_service.save_and_verify_api_token("valid_token")

            assert status.is_configured is True
            assert status.is_valid is True
            assert status.error_message is None

    @pytest.mark.asyncio
    async def test_save_and_verify_mocked_failure(self, tmdb_service):
        """Test save with mocked failed verification."""
        with patch.object(
            tmdb_service, "verify_api_token", new_callable=AsyncMock
        ) as mock_verify:
            mock_verify.return_value = (False, "Invalid API key")

            status = await tmdb_service.save_and_verify_api_token("invalid_token")

            assert status.is_configured is False
            assert status.is_valid is False
            assert "Invalid" in status.error_message

    @pytest.mark.asyncio
    async def test_verify_api_token_timeout(self, tmdb_service):
        """Test API token verification with timeout."""
        import httpx

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.TimeoutException("timeout")
            mock_client.return_value.__aenter__.return_value = mock_instance

            is_valid, error = await tmdb_service.verify_api_token("test_token")

            assert is_valid is False
            assert "超时" in error

    @pytest.mark.asyncio
    async def test_test_proxy_socks5_missing_support(self, tmdb_service):
        """Test SOCKS5 proxy reports missing runtime support clearly.

        通过模拟 httpx 抛出 socksio 缺失的 ImportError，使断言与
        运行环境是否安装 httpx[socks] 无关（确定性）。
        """
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.side_effect = ImportError(
                "Using SOCKS proxy, but the 'socksio' package is not installed."
            )

            success, message, latency = await tmdb_service.test_proxy("socks5://127.0.0.1:1080")

        assert success is False
        assert latency is None
        assert message == "测试失败: SOCKS5 代理缺少运行依赖，请安装 httpx[socks]"


class TestAdultAccessCheck:
    """Tests for TMDBService.check_adult_access（R18 能力探测）。

    探测走 _make_api_request，测试直接把它换掉：不再关心 httpx/网络，
    只断言各种返回组合下的结论是否成立。
    """

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "test.db"

    @pytest.fixture
    def tmdb_service(self, temp_db):
        """Provide a TMDBService instance."""
        return TMDBService(config_service=ConfigService(db_path=temp_db))

    @staticmethod
    def _response(status_code: int, results: list[dict] | None = None) -> MagicMock:
        """构造 httpx.Response 的最小替身（只用到 status_code 与 json）。"""
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = {"results": results or []}
        return response

    @pytest.mark.asyncio
    async def test_returns_none_without_token(self, tmdb_service):
        """未配置 Token 时无法判定（而不是断言未开启）。"""
        enabled, message = await tmdb_service.check_adult_access()

        assert enabled is None
        assert "未配置" in message

    @pytest.mark.asyncio
    async def test_detects_enabled_on_adult_hit(self, tmdb_service, config_service):
        """命中 adult=true 即判定为已开启，且不再消耗后续关键词。"""
        await config_service.save_api_token("eyJfake_token")
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = self._response(
                200,
                [{"id": 1, "name": "x", "adult": False}, {"id": 2, "name": "y", "adult": True}],
            )

            enabled, message = await tmdb_service.check_adult_access()

        assert enabled is True
        assert "已开启" in message
        assert mock_request.await_count == 1

    @pytest.mark.asyncio
    async def test_detects_disabled_when_probes_have_no_adult(self, tmdb_service, config_service):
        """探测有关键词结果但均无 adult：判定为未开启。"""
        await config_service.save_api_token("eyJfake_token")
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = self._response(
                200, [{"id": 1, "name": "x", "adult": False}]
            )

            enabled, message = await tmdb_service.check_adult_access()

        assert enabled is False
        assert "未开启" in message

    @pytest.mark.asyncio
    async def test_unknown_when_all_probes_empty(self, tmdb_service, config_service):
        """关键词全部搜不到东西时不能断言 R18 被关（可能只是词条变动）。"""
        await config_service.save_api_token("eyJfake_token")
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = self._response(200, [])

            enabled, message = await tmdb_service.check_adult_access()

        assert enabled is None
        assert "无法判断" in message

    @pytest.mark.asyncio
    async def test_unknown_on_401(self, tmdb_service, config_service):
        """凭据失效是认证问题，不能报成 R18 未开启。"""
        await config_service.save_api_token("eyJfake_token")
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.return_value = self._response(401)

            enabled, message = await tmdb_service.check_adult_access()

        assert enabled is None
        assert "Token 无效" in message

    @pytest.mark.asyncio
    async def test_unknown_on_timeout(self, tmdb_service, config_service):
        """超时同样属于「无法判定」。"""
        await config_service.save_api_token("eyJfake_token")
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = TMDBTimeoutError("/search/tv")

            enabled, message = await tmdb_service.check_adult_access()

        assert enabled is None
        assert "超时" in message

    @pytest.mark.asyncio
    async def test_refresh_token_status_persists_adult_result(
        self, tmdb_service, config_service
    ):
        """重新检测：远端重验 + 重探 R18，并把结论写进状态。"""
        await config_service.save_api_token("eyJfake_token")
        with (
            patch.object(
                tmdb_service, "verify_api_token", new_callable=AsyncMock
            ) as mock_verify,
            patch.object(
                tmdb_service, "check_adult_access", new_callable=AsyncMock
            ) as mock_adult,
        ):
            mock_verify.return_value = (True, None)
            mock_adult.return_value = (True, "已开启（关键词「hentai」命中成人内容）")

            status = await tmdb_service.refresh_token_status()

        assert status.is_valid is True
        assert status.adult_enabled is True
        assert status.adult_checked_at is not None

    @pytest.mark.asyncio
    async def test_refresh_token_status_keeps_old_adult_on_invalid_token(
        self, tmdb_service, config_service
    ):
        """Token 失效时不重探 R18（失效不是 R18 状态变化），旧结论保留。"""
        await config_service.save_api_token("eyJfake_token")
        await config_service.set_api_token_verified(True)
        await config_service.save_adult_status(True, "已开启")

        with (
            patch.object(
                tmdb_service, "verify_api_token", new_callable=AsyncMock
            ) as mock_verify,
            patch.object(
                tmdb_service, "check_adult_access", new_callable=AsyncMock
            ) as mock_adult,
        ):
            mock_verify.return_value = (False, "API Token 无效或已过期")

            status = await tmdb_service.refresh_token_status()

        assert status.is_valid is False
        assert status.adult_enabled is True
        mock_adult.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_token_clears_adult_status(self, tmdb_service, config_service):
        """删 Token 同时清掉 R18 结论：换了 Token 旧结论可能不成立。"""
        await config_service.save_api_token("eyJfake_token")
        await config_service.save_adult_status(True, "已开启")

        await tmdb_service.delete_api_token()
        enabled, message, checked_at = await config_service.get_adult_status()

        assert enabled is None
        assert message is None
        assert checked_at is None


class TestTMDBServiceSearch:
    """Tests for TMDBService search and metadata methods."""

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            yield db_path

    @pytest.fixture
    def config_service(self, temp_db):
        """Provide a ConfigService instance."""
        return ConfigService(db_path=temp_db)

    @pytest.fixture
    def tmdb_service(self, config_service):
        """Provide a TMDBService instance."""
        return TMDBService(config_service=config_service)

    @pytest.mark.asyncio
    async def test_search_series_by_api_mocked(self, tmdb_service):
        """Test search with mocked API response."""
        mock_json = {
            "results": [
                {
                    "id": 1396,
                    "name": "Breaking Bad",
                    "original_name": "Breaking Bad",
                    "first_air_date": "2008-01-20",
                    "poster_path": "/poster.jpg",
                    "overview": "A chemistry teacher...",
                    "vote_average": 8.9,
                    "adult": False,
                }
            ],
            "total_results": 1,
        }

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json
            mock_request.return_value = mock_response

            result = await tmdb_service.search_series_by_api("Breaking Bad")

            assert result.query == "Breaking Bad"
            assert result.total_results == 1
            assert len(result.results) == 1
            assert result.results[0].id == 1396
            assert result.results[0].name == "Breaking Bad"

    @pytest.mark.asyncio
    async def test_search_series_by_api_timeout(self, tmdb_service):
        """Test search with timeout."""
        import httpx

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_request.side_effect = httpx.TimeoutException("timeout")

            with pytest.raises(httpx.TimeoutException):
                await tmdb_service.search_series_by_api("test")

    @pytest.mark.asyncio
    async def test_get_series_by_api_mocked(self, tmdb_service):
        """Test getting series with mocked API response."""
        mock_json = {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "overview": "A chemistry teacher...",
            "first_air_date": "2008-01-20",
            "vote_average": 8.9,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [{"id": 18, "name": "Drama"}],
            "status": "Ended",
            "number_of_seasons": 5,
            "number_of_episodes": 62,
            "seasons": [
                {
                    "season_number": 1,
                    "name": "Season 1",
                    "episode_count": 7,
                }
            ],
        }

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json
            mock_request.return_value = mock_response

            result = await tmdb_service.get_series_by_api(1396)

            assert result is not None
            assert result.id == 1396
            assert result.name == "Breaking Bad"
            assert len(result.genres) == 1
            assert result.genres[0] == "Drama"

    @pytest.mark.asyncio
    async def test_get_series_by_api_not_found(self, tmdb_service):
        """Test getting non-existent series."""
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            result = await tmdb_service.get_series_by_api(99999999)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_season_by_api_mocked(self, tmdb_service):
        """Test getting season with mocked API response."""
        mock_json = {
            "season_number": 1,
            "name": "Season 1",
            "overview": "The first season...",
            "air_date": "2008-01-20",
            "poster_path": "/season1.jpg",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Pilot",
                    "overview": "The first episode.",
                    "air_date": "2008-01-20",
                    "vote_average": 8.5,
                    "still_path": "/ep1.jpg",
                },
                {
                    "episode_number": 2,
                    "name": "Cat's in the Bag",
                    "overview": "The second episode.",
                    "air_date": "2008-01-27",
                    "vote_average": 8.3,
                    "still_path": "/ep2.jpg",
                },
            ],
        }

        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = mock_json
            mock_request.return_value = mock_response

            result = await tmdb_service.get_season_by_api(1396, 1)

            assert result is not None
            assert result.season_number == 1
            assert result.name == "Season 1"
            assert len(result.episodes) == 2
            assert result.episodes[0].name == "Pilot"

    @pytest.mark.asyncio
    async def test_get_season_by_api_not_found(self, tmdb_service):
        """Test getting non-existent season."""
        with patch.object(
            tmdb_service, "_make_api_request", new_callable=AsyncMock
        ) as mock_request:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_request.return_value = mock_response

            result = await tmdb_service.get_season_by_api(1396, 99)

            assert result is None

    def test_parse_series_json(self, tmdb_service):
        """Test parsing series JSON."""
        data = {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "overview": "A chemistry teacher...",
            "first_air_date": "2008-01-20",
            "vote_average": 8.9,
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "genres": [{"id": 18, "name": "Drama"}],
            "status": "Ended",
            "number_of_seasons": 5,
            "number_of_episodes": 62,
            "seasons": [],
        }
        result = tmdb_service._parse_series_json(data)

        assert result.id == 1396
        assert result.name == "Breaking Bad"
        assert result.vote_average == 8.9
        assert result.genres == ["Drama"]

    def test_parse_season_json(self, tmdb_service):
        """Test parsing season JSON."""
        data = {
            "season_number": 1,
            "name": "Season 1",
            "overview": "The first season...",
            "air_date": "2008-01-20",
            "poster_path": "/season1.jpg",
            "episodes": [
                {
                    "episode_number": 1,
                    "name": "Pilot",
                    "overview": "The first episode.",
                    "air_date": "2008-01-20",
                    "vote_average": 8.5,
                    "still_path": "/ep1.jpg",
                }
            ],
        }
        result = tmdb_service._parse_season_json(data)

        assert result.season_number == 1
        assert result.name == "Season 1"
        assert len(result.episodes) == 1
        assert result.episodes[0].name == "Pilot"
