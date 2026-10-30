"""Unit tests for Images API endpoints."""

import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.main import app
from server.models.image import BatchDownloadResponse, ImageDownloadResult


@pytest.fixture
def client(override_auth):
    """Provide a test client."""
    return TestClient(app)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestImagesAPI:
    """Tests for /api/images endpoints."""

    def test_download_single_image_mocked(self, client, temp_dir):
        """Test single image download with mocked service."""
        mock_result = ImageDownloadResult(
            url="https://example.com/image.jpg",
            save_path=f"{temp_dir}/poster.jpg",
            success=True,
        )

        with patch(
            "server.domain.artifacts.image_service.ImageService.download_image",
            new_callable=AsyncMock,
        ) as mock_download:
            mock_download.return_value = mock_result

            response = client.post(
                "/api/images/download",
                json={
                    "url": "https://example.com/image.jpg",
                    "save_path": temp_dir,
                    "filename": "poster.jpg",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True

    def test_download_single_image_failure(self, client, temp_dir):
        """Test single image download failure."""
        mock_result = ImageDownloadResult(
            url="https://example.com/notfound.jpg",
            save_path=f"{temp_dir}/poster.jpg",
            success=False,
            error="Image not found (404)",
        )

        with patch(
            "server.domain.artifacts.image_service.ImageService.download_image",
            new_callable=AsyncMock,
        ) as mock_download:
            mock_download.return_value = mock_result

            response = client.post(
                "/api/images/download",
                json={
                    "url": "https://example.com/notfound.jpg",
                    "save_path": temp_dir,
                    "filename": "poster.jpg",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is False
            assert "404" in data["error"]

    def test_download_batch_mocked(self, client, temp_dir):
        """Test batch download with mocked service."""
        mock_response = BatchDownloadResponse(
            total=2,
            success=2,
            failed=0,
            results=[
                ImageDownloadResult(
                    url="https://example.com/1.jpg",
                    save_path=f"{temp_dir}/1.jpg",
                    success=True,
                ),
                ImageDownloadResult(
                    url="https://example.com/2.jpg",
                    save_path=f"{temp_dir}/2.jpg",
                    success=True,
                ),
            ],
        )

        with patch(
            "server.domain.artifacts.image_service.ImageService.download_batch",
            new_callable=AsyncMock,
        ) as mock_download:
            mock_download.return_value = mock_response

            response = client.post(
                "/api/images/download/batch",
                json={
                    "images": [
                        {
                            "url": "https://example.com/1.jpg",
                            "save_path": temp_dir,
                            "filename": "1.jpg",
                        },
                        {
                            "url": "https://example.com/2.jpg",
                            "save_path": temp_dir,
                            "filename": "2.jpg",
                        },
                    ],
                    "concurrency": 2,
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 2
            assert data["success"] == 2
            assert data["failed"] == 0

    def test_download_batch_empty(self, client):
        """Test batch download with empty list."""
        mock_response = BatchDownloadResponse(
            total=0,
            success=0,
            failed=0,
            results=[],
        )

        with patch(
            "server.domain.artifacts.image_service.ImageService.download_batch",
            new_callable=AsyncMock,
        ) as mock_download:
            mock_download.return_value = mock_response

            response = client.post(
                "/api/images/download/batch",
                json={"images": []},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 0

    def test_download_missing_required_fields(self, client):
        """Test download with missing required fields."""
        response = client.post(
            "/api/images/download",
            json={"url": "https://example.com/image.jpg"},
        )

        assert response.status_code == 422

    def test_download_batch_with_concurrency(self, client, temp_dir):
        """Test batch download with custom concurrency."""
        mock_response = BatchDownloadResponse(
            total=1,
            success=1,
            failed=0,
            results=[
                ImageDownloadResult(
                    url="https://example.com/1.jpg",
                    save_path=f"{temp_dir}/1.jpg",
                    success=True,
                ),
            ],
        )

        with patch(
            "server.domain.artifacts.image_service.ImageService.download_batch",
            new_callable=AsyncMock,
        ) as mock_download:
            mock_download.return_value = mock_response

            response = client.post(
                "/api/images/download/batch",
                json={
                    "images": [
                        {
                            "url": "https://example.com/1.jpg",
                            "save_path": temp_dir,
                            "filename": "1.jpg",
                        },
                    ],
                    "concurrency": 5,
                },
            )

            assert response.status_code == 200
            # Verify concurrency was passed
            mock_download.assert_called_once()
            call_args = mock_download.call_args
            assert call_args[1]["concurrency"] == 5
