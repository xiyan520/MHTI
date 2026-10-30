"""Unit tests for Rename API endpoints."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.bootstrap import get_rename_service
from server.domain.artifacts.rename_service import RenameService
from server.domain.system.template_service import TemplateService
from server.main import app


@pytest.fixture
def client(temp_db, override_auth):
    """Provide a test client."""
    rename_service = RenameService(template_service=TemplateService(db_path=temp_db))

    def override_rename_service():
        return rename_service

    app.dependency_overrides[get_rename_service] = override_rename_service
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_video(temp_dir):
    """Create a sample video file for testing."""
    video_path = Path(temp_dir) / "test_video.mp4"
    video_path.write_bytes(b"fake video content")
    return str(video_path)


class TestRenameAPI:
    """Tests for /api/rename endpoints."""

    def test_preview_rename(self, client, sample_video, temp_dir):
        """Test preview endpoint."""
        response = client.post(
            "/api/rename/preview",
            json={
                "source_path": sample_video,
                "title": "Test Show",
                "season": 1,
                "episode": 5,
                "episode_title": "Pilot",
                "output_dir": temp_dir,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["source_path"] == sample_video
        assert "Test Show" in data["dest_path"]
        assert "S01E05" in data["new_filename"]
        assert "Pilot" in data["new_filename"]

    def test_preview_without_episode_title(self, client, sample_video, temp_dir):
        """Test preview without episode title."""
        response = client.post(
            "/api/rename/preview",
            json={
                "source_path": sample_video,
                "title": "Another Show",
                "season": 2,
                "episode": 10,
                "output_dir": temp_dir,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "S02E10" in data["new_filename"]

    def test_execute_rename(self, client, sample_video, temp_dir):
        """Test execute rename endpoint."""
        output_dir = str(Path(temp_dir) / "output")
        response = client.post(
            "/api/rename/execute",
            json={
                "source_path": sample_video,
                "title": "Test Show",
                "season": 1,
                "episode": 1,
                "output_dir": output_dir,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert Path(data["dest_path"]).exists()

    def test_execute_rename_with_backup(self, client, sample_video, temp_dir):
        """Test execute rename with backup."""
        output_dir = str(Path(temp_dir) / "output")
        response = client.post(
            "/api/rename/execute",
            params={"create_backup": True},
            json={
                "source_path": sample_video,
                "title": "Test Show",
                "season": 1,
                "episode": 1,
                "output_dir": output_dir,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["backup_path"] is not None

    def test_execute_rename_not_found(self, client, temp_dir):
        """Test execute rename with non-existent file."""
        response = client.post(
            "/api/rename/execute",
            json={
                "source_path": "/nonexistent/file.mp4",
                "title": "Test Show",
                "season": 1,
                "episode": 1,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert "not found" in data["error"].lower()

    def test_batch_rename(self, client, temp_dir):
        """Test batch rename endpoint."""
        # Create source files
        items = []
        for i in range(2):
            source_path = Path(temp_dir) / f"video{i}.mp4"
            source_path.write_bytes(b"video content")
            items.append({
                "source_path": str(source_path),
                "title": "Batch Show",
                "season": 1,
                "episode": i + 1,
                "output_dir": str(Path(temp_dir) / "output"),
            })

        response = client.post(
            "/api/rename/batch",
            json={
                "items": items,
                "create_backup": False,
                "dry_run": False,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert data["success"] == 2
        assert data["failed"] == 0

    def test_batch_rename_dry_run(self, client, temp_dir):
        """Test batch rename in dry-run mode."""
        # Create source file
        source_path = Path(temp_dir) / "video.mp4"
        source_path.write_bytes(b"video content")

        response = client.post(
            "/api/rename/batch",
            json={
                "items": [
                    {
                        "source_path": str(source_path),
                        "title": "Dry Run Show",
                        "season": 1,
                        "episode": 1,
                        "output_dir": str(Path(temp_dir) / "output"),
                    }
                ],
                "dry_run": True,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["previews"] is not None
        assert len(data["previews"]) == 1
        # Original file should still exist
        assert source_path.exists()

    def test_batch_rename_empty(self, client):
        """Test batch rename with empty list."""
        response = client.post(
            "/api/rename/batch",
            json={
                "items": [],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0

    def test_preview_missing_required_fields(self, client):
        """Test preview with missing required fields."""
        response = client.post(
            "/api/rename/preview",
            json={
                "source_path": "/some/path.mp4",
                # Missing title, season, episode
            },
        )

        assert response.status_code == 422
