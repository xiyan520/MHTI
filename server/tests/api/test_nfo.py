"""Unit tests for NFO API endpoints."""


import pytest
from fastapi.testclient import TestClient

from server.main import app


@pytest.fixture
def client(override_auth):
    """Provide a test client."""
    return TestClient(app)


class TestNFOAPI:
    """Tests for /api/nfo endpoints."""

    def test_generate_tvshow_nfo(self, client):
        """Test POST /api/nfo/tvshow."""
        response = client.post(
            "/api/nfo/tvshow",
            json={
                "title": "Breaking Bad",
                "original_title": "Breaking Bad",
                "rating": 8.9,
                "year": 2008,
                "tmdb_id": 1396,
                "genres": ["Drama", "Crime"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "nfo" in data
        assert "filename" in data
        assert data["filename"] == "tvshow.nfo"
        assert "<tvshow>" in data["nfo"]
        assert "<title>Breaking Bad</title>" in data["nfo"]

    def test_generate_season_nfo(self, client):
        """Test POST /api/nfo/season."""
        response = client.post(
            "/api/nfo/season",
            json={
                "season_number": 1,
                "title": "Season 1",
                "plot": "The first season.",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "season.nfo"
        assert "<season>" in data["nfo"]
        assert "<seasonnumber>1</seasonnumber>" in data["nfo"]

    def test_generate_episode_nfo(self, client):
        """Test POST /api/nfo/episode."""
        response = client.post(
            "/api/nfo/episode",
            json={
                "title": "Pilot",
                "season": 1,
                "episode": 1,
                "plot": "The first episode.",
                "rating": 8.5,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "S01E01.nfo"
        assert "<episodedetails>" in data["nfo"]
        assert "<title>Pilot</title>" in data["nfo"]

    def test_generate_episode_nfo_filename_format(self, client):
        """Test episode NFO filename format."""
        response = client.post(
            "/api/nfo/episode",
            json={
                "title": "Episode Title",
                "season": 5,
                "episode": 12,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "S05E12.nfo"

    def test_generate_tvshow_nfo_minimal(self, client):
        """Test tvshow NFO with minimal required fields."""
        response = client.post(
            "/api/nfo/tvshow",
            json={"title": "Test Show"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "<title>Test Show</title>" in data["nfo"]

    def test_generate_tvshow_nfo_missing_title(self, client):
        """Test tvshow NFO without required title."""
        response = client.post(
            "/api/nfo/tvshow",
            json={"year": 2024},
        )

        assert response.status_code == 422  # Validation error

    def test_generate_episode_nfo_missing_fields(self, client):
        """Test episode NFO without required fields."""
        response = client.post(
            "/api/nfo/episode",
            json={"title": "Test"},  # Missing season and episode
        )

        assert response.status_code == 422

    def test_generate_nfo_with_chinese(self, client):
        """Test NFO generation with Chinese characters."""
        response = client.post(
            "/api/nfo/tvshow",
            json={
                "title": "绝命毒师",
                "original_title": "Breaking Bad",
                "plot": "一位化学老师成为毒贩。",
                "genres": ["剧情", "犯罪"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "绝命毒师" in data["nfo"]
        assert "一位化学老师成为毒贩" in data["nfo"]

    def test_generate_nfo_with_date(self, client):
        """Test NFO generation with date fields."""
        response = client.post(
            "/api/nfo/tvshow",
            json={
                "title": "Test Show",
                "premiered": "2024-01-15",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "<premiered>2024-01-15</premiered>" in data["nfo"]
