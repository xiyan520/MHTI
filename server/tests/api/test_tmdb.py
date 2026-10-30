"""Unit tests for TMDB API endpoints.

测试 /api/tmdb 路由的各项功能。
使用 conftest.py 中的 auth_client fixture 绕过认证。
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.bootstrap import get_tmdb_service
from server.common.exceptions import TMDBConnectionError, TMDBTimeoutError
from server.domain.metadata.tmdb_service import TMDBService
from server.domain.system.config_service import ConfigService
from server.main import app
from server.models.tmdb import (
    TMDBEpisode,
    TMDBSearchResponse,
    TMDBSearchResult,
    TMDBSeason,
    TMDBSeries,
)


@pytest.fixture
def tmdb_client(temp_db: Path, override_auth) -> TestClient:
    """
    提供带有 TMDB 服务和认证覆盖的测试客户端。

    Args:
        temp_db: Temporary database path.
        override_auth: Authentication override fixture.

    Returns:
        Configured test client.
    """
    config_service = ConfigService(db_path=temp_db)
    tmdb_service = TMDBService(config_service=config_service)

    def override_tmdb_service():
        return tmdb_service

    app.dependency_overrides[get_tmdb_service] = override_tmdb_service
    yield TestClient(app)
    # 清理所有覆盖
    app.dependency_overrides.clear()


class TestTMDBSearchAPI:
    """Tests for /api/tmdb/search endpoint."""

    def test_search_empty_query(self, tmdb_client):
        """Test search with empty query returns 422."""
        response = tmdb_client.get("/api/tmdb/search?q=")
        assert response.status_code == 422

    def test_search_missing_query(self, tmdb_client):
        """Test search without query parameter returns 422."""
        response = tmdb_client.get("/api/tmdb/search")
        assert response.status_code == 422

    def test_search_mocked_success(self, tmdb_client):
        """Test search with mocked successful response."""
        mock_response = TMDBSearchResponse(
            query="Breaking Bad",
            total_results=1,
            results=[
                TMDBSearchResult(
                    id=1396,
                    name="Breaking Bad",
                    original_name="Breaking Bad",
                    vote_average=8.9,
                )
            ],
        )

        with patch.object(
            TMDBService, "search_series_by_api", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_response

            response = tmdb_client.get("/api/tmdb/search?q=Breaking+Bad")

            assert response.status_code == 200
            data = response.json()
            assert data["query"] == "Breaking Bad"
            assert data["total_results"] == 1
            assert len(data["results"]) == 1
            assert data["results"][0]["id"] == 1396

    def test_search_mocked_timeout(self, tmdb_client):
        """Test search with timeout error returns 408."""
        with patch.object(
            TMDBService, "search_series_by_api", new_callable=AsyncMock
        ) as mock_search:
            mock_search.side_effect = TMDBTimeoutError("/search/tv")

            response = tmdb_client.get("/api/tmdb/search?q=test")

            assert response.status_code == 408
            data = response.json()
            assert "error" in data
            assert "超时" in data["error"]["message"]

    def test_search_mocked_connection_error(self, tmdb_client):
        """Test search with connection error returns 502."""
        with patch.object(
            TMDBService, "search_series_by_api", new_callable=AsyncMock
        ) as mock_search:
            mock_search.side_effect = TMDBConnectionError("connection failed")

            response = tmdb_client.get("/api/tmdb/search?q=test")

            assert response.status_code == 502
            data = response.json()
            assert "error" in data
            assert "连接失败" in data["error"]["message"]

    def test_search_with_language_parameter(self, tmdb_client):
        """Test search with custom language parameter."""
        mock_response = TMDBSearchResponse(
            query="test",
            total_results=0,
            results=[],
        )

        with patch.object(
            TMDBService, "search_series_by_api", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = mock_response

            response = tmdb_client.get("/api/tmdb/search?q=test&language=en-US")

            assert response.status_code == 200
            mock_search.assert_called_once_with(query="test", language="en-US")


class TestTMDBSeriesAPI:
    """Tests for /api/tmdb/series/{tmdb_id} endpoint."""

    def test_get_series_mocked_success(self, tmdb_client):
        """Test getting series with mocked response."""
        mock_series = TMDBSeries(
            id=1396,
            name="Breaking Bad",
            original_name="Breaking Bad",
            overview="A high school chemistry teacher...",
            vote_average=8.9,
            genres=["Drama", "Crime"],
            status="Ended",
            number_of_seasons=5,
            seasons=[
                TMDBSeason(season_number=1, name="Season 1"),
                TMDBSeason(season_number=2, name="Season 2"),
            ],
        )

        with patch.object(
            TMDBService, "get_series_with_episodes", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_series

            response = tmdb_client.get("/api/tmdb/series/1396")

            assert response.status_code == 200
            data = response.json()
            assert data["id"] == 1396
            assert data["name"] == "Breaking Bad"
            assert len(data["seasons"]) == 2

    def test_get_series_not_found(self, tmdb_client):
        """Test getting non-existent series returns 404."""
        with patch.object(
            TMDBService, "get_series_with_episodes", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = None

            response = tmdb_client.get("/api/tmdb/series/99999999")

            assert response.status_code == 404
            data = response.json()
            assert "error" in data
            assert "未找到" in data["error"]["message"]

    def test_get_series_timeout(self, tmdb_client):
        """Test getting series with timeout returns 408."""
        with patch.object(
            TMDBService, "get_series_with_episodes", new_callable=AsyncMock
        ) as mock_get:
            mock_get.side_effect = TMDBTimeoutError("/tv/1396")

            response = tmdb_client.get("/api/tmdb/series/1396")

            assert response.status_code == 408

    def test_get_series_without_episodes(self, tmdb_client):
        """Test getting series without episode details."""
        mock_series = TMDBSeries(
            id=1396,
            name="Breaking Bad",
        )

        with patch.object(
            TMDBService, "get_series_with_episodes", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_series

            response = tmdb_client.get("/api/tmdb/series/1396?include_episodes=false")

            assert response.status_code == 200
            mock_get.assert_called_once_with(
                tmdb_id=1396, language="zh-CN", include_episodes=False
            )


class TestTMDBSeasonAPI:
    """Tests for /api/tmdb/series/{tmdb_id}/season/{season_number} endpoint."""

    def test_get_season_mocked_success(self, tmdb_client):
        """Test getting season with mocked response."""
        mock_season = TMDBSeason(
            season_number=1,
            name="Season 1",
            overview="The first season...",
            episode_count=7,
            episodes=[
                TMDBEpisode(episode_number=1, name="Pilot"),
                TMDBEpisode(episode_number=2, name="Cat's in the Bag..."),
            ],
        )

        with patch.object(
            TMDBService, "get_season_by_api", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_season

            response = tmdb_client.get("/api/tmdb/series/1396/season/1")

            assert response.status_code == 200
            data = response.json()
            assert data["season_number"] == 1
            assert data["episode_count"] == 7
            assert len(data["episodes"]) == 2

    def test_get_season_not_found(self, tmdb_client):
        """Test getting non-existent season returns 404."""
        with patch.object(
            TMDBService, "get_season_by_api", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = None

            response = tmdb_client.get("/api/tmdb/series/1396/season/99")

            assert response.status_code == 404

    def test_get_season_with_language(self, tmdb_client):
        """Test getting season with custom language."""
        mock_season = TMDBSeason(season_number=1, name="第一季")

        with patch.object(
            TMDBService, "get_season_by_api", new_callable=AsyncMock
        ) as mock_get:
            mock_get.return_value = mock_season

            response = tmdb_client.get("/api/tmdb/series/1396/season/1?language=zh-CN")

            assert response.status_code == 200
            mock_get.assert_called_once_with(
                tmdb_id=1396, season_number=1, language="zh-CN"
            )

    def test_get_season_invalid_season_number(self, tmdb_client):
        """Test getting season with invalid season number."""
        response = tmdb_client.get("/api/tmdb/series/1396/season/abc")
        assert response.status_code == 422
