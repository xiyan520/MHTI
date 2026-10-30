"""Unit tests for Subtitles API endpoints."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.main import app


@pytest.fixture
def client(override_auth):
    """Provide a test client."""
    return TestClient(app)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestSubtitlesAPI:
    """Tests for /api/subtitles endpoints."""

    def test_scan_subtitles(self, client, temp_dir):
        """Test scanning for subtitles."""
        # Create subtitle files
        (Path(temp_dir) / "video.srt").write_text("subtitle")
        (Path(temp_dir) / "video.ass").write_text("subtitle")

        response = client.post(
            "/api/subtitles/scan",
            json={"folder_path": temp_dir},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2

    def test_scan_empty_folder(self, client, temp_dir):
        """Test scanning empty folder."""
        response = client.post(
            "/api/subtitles/scan",
            json={"folder_path": temp_dir},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["subtitles"] == []

    def test_scan_with_language(self, client, temp_dir):
        """Test scanning detects language."""
        (Path(temp_dir) / "video.chs.srt").write_text("subtitle")

        response = client.post(
            "/api/subtitles/scan",
            json={"folder_path": temp_dir},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["subtitles"][0]["language"] == "chs"

    def test_associate_subtitles(self, client, temp_dir):
        """Test associating subtitles with videos."""
        (Path(temp_dir) / "EP01.mp4").write_bytes(b"video")
        (Path(temp_dir) / "EP01.chs.srt").write_text("subtitle")
        (Path(temp_dir) / "EP01.eng.srt").write_text("subtitle")

        response = client.post(
            "/api/subtitles/associate",
            json={"folder_path": temp_dir},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["associations"]) == 1
        assert data["associations"][0]["video"] == "EP01.mp4"
        assert len(data["associations"][0]["subtitles"]) == 2

    def test_associate_specific_videos(self, client, temp_dir):
        """Test associating with specific video list."""
        (Path(temp_dir) / "EP01.srt").write_text("subtitle")
        (Path(temp_dir) / "EP02.srt").write_text("subtitle")

        response = client.post(
            "/api/subtitles/associate",
            json={
                "folder_path": temp_dir,
                "video_files": ["EP01.mp4", "EP02.mp4"],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["associations"]) == 2

    def test_rename_subtitle(self, client, temp_dir):
        """Test renaming a subtitle file."""
        source = Path(temp_dir) / "EP01.chs.srt"
        source.write_text("subtitle")

        response = client.post(
            "/api/subtitles/rename",
            json={
                "subtitle_path": str(source),
                "new_video_name": "Show Name - S01E01",
                "preserve_language": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Show Name - S01E01.chs.srt" in data["dest_path"]

    def test_rename_not_found(self, client):
        """Test renaming non-existent subtitle."""
        response = client.post(
            "/api/subtitles/rename",
            json={
                "subtitle_path": "/nonexistent/file.srt",
                "new_video_name": "New Name",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_batch_rename(self, client, temp_dir):
        """Test batch renaming subtitles."""
        for i in range(2):
            (Path(temp_dir) / f"EP0{i}.srt").write_text("subtitle")

        response = client.post(
            "/api/subtitles/rename/batch",
            json={
                "items": [
                    {
                        "subtitle_path": str(Path(temp_dir) / f"EP0{i}.srt"),
                        "new_video_name": f"Show - S01E0{i}",
                        "preserve_language": True,
                    }
                    for i in range(2)
                ],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["success"] == 2
        assert data["failed"] == 0

    def test_batch_rename_empty(self, client):
        """Test batch rename with empty list."""
        response = client.post(
            "/api/subtitles/rename/batch",
            json={"items": []},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
