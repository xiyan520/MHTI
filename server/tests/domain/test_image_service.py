"""Unit tests for ImageService."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from server.domain.artifacts.image_service import ImageService
from server.models.config import ProxyConfig
from server.models.image import ImageDownloadRequest, ImageSize
from server.models.system import SystemConfig


@pytest.fixture
def mock_config_service():
    """Provide a mock ConfigService."""
    config_service = MagicMock()
    config_service.get_system_config = AsyncMock(return_value=SystemConfig(
        retry_count=3,
        concurrent_downloads=3,
        task_timeout=30
    ))
    config_service.get_proxy_config = AsyncMock(return_value=ProxyConfig())
    return config_service


@pytest.fixture
def image_service(mock_config_service):
    """Provide an ImageService instance."""
    return ImageService(config_service=mock_config_service)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestImageServiceBasic:
    """Basic tests for ImageService."""

    def test_get_full_image_url(self, image_service):
        """Test full URL generation."""
        url = image_service.get_full_image_url("/abc123.jpg", ImageSize.W500)
        assert url == "https://image.tmdb.org/t/p/w500/abc123.jpg"

    def test_get_full_image_url_original(self, image_service):
        """Test full URL with original size."""
        url = image_service.get_full_image_url("/poster.jpg", ImageSize.ORIGINAL)
        assert url == "https://image.tmdb.org/t/p/original/poster.jpg"

    def test_get_full_image_url_none(self, image_service):
        """Test URL generation with None path."""
        url = image_service.get_full_image_url(None)
        assert url is None

    def test_get_full_image_url_empty(self, image_service):
        """Test URL generation with empty path."""
        url = image_service.get_full_image_url("")
        assert url is None


class TestImageServiceDownload:
    """Tests for single image download."""

    @pytest.mark.asyncio
    async def test_download_image_success(self, image_service, temp_dir):
        """Test successful image download."""
        mock_content = b"fake image data"

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = mock_content
            mock_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            result = await image_service.download_image(
                url="https://example.com/image.jpg",
                save_path=temp_dir,
                filename="test.jpg",
            )

            assert result.success is True
            assert Path(result.save_path).exists()
            assert Path(result.save_path).read_bytes() == mock_content

    @pytest.mark.asyncio
    async def test_download_image_404(self, image_service, temp_dir):
        """Test download with 404 response."""
        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 404

            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            result = await image_service.download_image(
                url="https://example.com/notfound.jpg",
                save_path=temp_dir,
                filename="test.jpg",
            )

            assert result.success is False
            assert "404" in result.error

    @pytest.mark.asyncio
    async def test_download_image_timeout_retry(self, image_service, mock_config_service, temp_dir):
        """Test download retry on timeout."""
        # Use fewer retries for testing
        mock_config_service.get_system_config = AsyncMock(return_value=SystemConfig(
            retry_count=2,
            concurrent_downloads=3,
            task_timeout=30
        ))

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.TimeoutException("timeout")
            mock_client.return_value.__aenter__.return_value = mock_instance

            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await image_service.download_image(
                    url="https://example.com/image.jpg",
                    save_path=temp_dir,
                    filename="test.jpg",
                )

                assert result.success is False
                assert "timeout" in result.error.lower()

    @pytest.mark.asyncio
    async def test_download_image_connection_error(self, image_service, mock_config_service, temp_dir):
        """Test download with connection error."""
        # Use fewer retries for testing
        mock_config_service.get_system_config = AsyncMock(return_value=SystemConfig(
            retry_count=1,
            concurrent_downloads=3,
            task_timeout=30
        ))

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = httpx.RequestError("connection failed")
            mock_client.return_value.__aenter__.return_value = mock_instance

            result = await image_service.download_image(
                url="https://example.com/image.jpg",
                save_path=temp_dir,
                filename="test.jpg",
            )

            assert result.success is False
            assert "connection" in result.error.lower()


class TestImageServiceBatch:
    """Tests for batch download."""

    @pytest.mark.asyncio
    async def test_download_batch_empty(self, image_service):
        """Test batch download with empty list."""
        result = await image_service.download_batch([])

        assert result.total == 0
        assert result.success == 0
        assert result.failed == 0
        assert result.results == []

    @pytest.mark.asyncio
    async def test_download_batch_success(self, image_service, temp_dir):
        """Test successful batch download."""
        mock_content = b"fake image data"

        with patch("httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.content = mock_content
            mock_response.raise_for_status = MagicMock()

            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_client.return_value.__aenter__.return_value = mock_instance

            requests = [
                ImageDownloadRequest(
                    url="https://example.com/image1.jpg",
                    save_path=temp_dir,
                    filename="image1.jpg",
                ),
                ImageDownloadRequest(
                    url="https://example.com/image2.jpg",
                    save_path=temp_dir,
                    filename="image2.jpg",
                ),
            ]

            result = await image_service.download_batch(requests, concurrency=2)

            assert result.total == 2
            assert result.success == 2
            assert result.failed == 0

    @pytest.mark.asyncio
    async def test_download_batch_partial_failure(self, image_service, temp_dir):
        """Test batch download with some failures."""
        mock_content = b"fake image data"
        call_count = 0

        async def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            if call_count == 1:
                mock_response.status_code = 200
                mock_response.content = mock_content
                mock_response.raise_for_status = MagicMock()
            else:
                mock_response.status_code = 404
            return mock_response

        with patch("httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = mock_get
            mock_client.return_value.__aenter__.return_value = mock_instance

            requests = [
                ImageDownloadRequest(
                    url="https://example.com/image1.jpg",
                    save_path=temp_dir,
                    filename="image1.jpg",
                ),
                ImageDownloadRequest(
                    url="https://example.com/image2.jpg",
                    save_path=temp_dir,
                    filename="image2.jpg",
                ),
            ]

            result = await image_service.download_batch(requests, concurrency=1)

            assert result.total == 2
            assert result.success == 1
            assert result.failed == 1


class TestImageServiceHelpers:
    """Tests for helper methods."""

    def test_generate_series_image_requests(self, image_service):
        """Test series image request generation."""
        requests = image_service.generate_series_image_requests(
            save_path="/path/to/show",
            poster_path="/poster123.jpg",
            backdrop_path="/backdrop456.jpg",
        )

        assert len(requests) == 2
        assert requests[0].filename == "poster.jpg"
        assert requests[1].filename == "backdrop.jpg"
        assert "w500" in requests[0].url
        assert "w780" in requests[1].url  # Backdrop uses larger size

    def test_generate_series_image_requests_no_backdrop(self, image_service):
        """Test series image request with only poster."""
        requests = image_service.generate_series_image_requests(
            save_path="/path/to/show",
            poster_path="/poster123.jpg",
        )

        assert len(requests) == 1
        assert requests[0].filename == "poster.jpg"

    def test_generate_series_image_requests_none(self, image_service):
        """Test series image request with no images."""
        requests = image_service.generate_series_image_requests(
            save_path="/path/to/show",
        )

        assert len(requests) == 0

    def test_generate_season_image_request(self, image_service):
        """Test season image request generation."""
        request = image_service.generate_season_image_request(
            save_path="/path/to/show",
            season_number=1,
            poster_path="/season1.jpg",
        )

        assert request is not None
        assert request.filename == "season01-poster.jpg"
        assert "w500" in request.url

    def test_generate_season_image_request_none(self, image_service):
        """Test season image request with no poster."""
        request = image_service.generate_season_image_request(
            save_path="/path/to/show",
            season_number=1,
            poster_path=None,
        )

        assert request is None

    def test_generate_episode_image_request(self, image_service):
        """Test episode image request generation."""
        request = image_service.generate_episode_image_request(
            save_path="/path/to/show/Season 01",
            season_number=1,
            episode_number=5,
            still_path="/still105.jpg",
        )

        assert request is not None
        assert request.filename == "S01E05.jpg"
        assert "w500" in request.url

    def test_generate_episode_image_request_none(self, image_service):
        """Test episode image request with no still."""
        request = image_service.generate_episode_image_request(
            save_path="/path/to/show/Season 01",
            season_number=1,
            episode_number=5,
            still_path=None,
        )

        assert request is None
