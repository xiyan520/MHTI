"""Image download service with retry and concurrency support."""

import asyncio
from pathlib import Path

import httpx

from server.domain.system.config_service import ConfigService
from server.infrastructure.http import get_client
from server.models.image import (
    BatchDownloadResponse,
    ImageDownloadRequest,
    ImageDownloadResult,
    ImageSize,
)

RETRY_DELAYS = [1, 2, 4]  # Exponential backoff

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class ImageService:
    """Service for downloading images with retry and concurrency support."""

    def __init__(self, config_service: ConfigService):
        """
        Initialize image service.

        Args:
            config_service: ConfigService for reading system config.
        """
        self.config_service = config_service
        self._headers = {
            "User-Agent": DEFAULT_USER_AGENT,
        }

    async def _get_system_config(self):
        """Get system config for timeout, retry, concurrency settings."""
        return await self.config_service.get_system_config()

    async def _get_proxy_url(self) -> str | None:
        """Get proxy URL from config."""
        config = await self.config_service.get_proxy_config()
        return config.get_url()

    def get_full_image_url(
        self,
        path: str | None,
        size: ImageSize = ImageSize.W500,
    ) -> str | None:
        """
        Get full TMDB image URL from path.

        Args:
            path: Image path from TMDB (e.g., "/abc123.jpg")
            size: Image size.

        Returns:
            Full image URL or None if path is empty.
        """
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE_URL}/{size.value}{path}"

    async def download_image(
        self,
        url: str,
        save_path: str,
        filename: str,
    ) -> ImageDownloadResult:
        """
        Download a single image with retry support.

        Args:
            url: Image URL to download.
            save_path: Directory to save the image.
            filename: Filename for the saved image.

        Returns:
            ImageDownloadResult with success status.
        """
        full_path = Path(save_path) / filename
        last_error: str | None = None

        config = await self._get_system_config()
        timeout = float(config.task_timeout)
        max_retries = config.retry_count
        proxy_url = await self._get_proxy_url()

        for attempt in range(max_retries):
            try:
                client = get_client(proxy=proxy_url, timeout=timeout)
                response = await client.get(url, headers=self._headers)

                if response.status_code == 404:
                    return ImageDownloadResult(
                        url=url,
                        save_path=str(full_path),
                        success=False,
                        error="Image not found (404)",
                    )

                response.raise_for_status()

                # Ensure directory exists
                full_path.parent.mkdir(parents=True, exist_ok=True)

                # Write image data
                with open(full_path, "wb") as f:
                    f.write(response.content)

                return ImageDownloadResult(
                    url=url,
                    save_path=str(full_path),
                    success=True,
                )

            except httpx.TimeoutException:
                last_error = "Download timeout"
            except httpx.HTTPStatusError as e:
                last_error = f"HTTP error: {e.response.status_code}"
            except httpx.RequestError as e:
                last_error = f"Connection error: {str(e)}"
            except OSError as e:
                # File system error - don't retry
                return ImageDownloadResult(
                    url=url,
                    save_path=str(full_path),
                    success=False,
                    error=f"File system error: {str(e)}",
                )

            # Wait before retry (if not last attempt)
            if attempt < max_retries - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

        return ImageDownloadResult(
            url=url,
            save_path=str(full_path),
            success=False,
            error=last_error,
        )

    async def download_batch(
        self,
        requests: list[ImageDownloadRequest],
        concurrency: int | None = None,
    ) -> BatchDownloadResponse:
        """
        Download multiple images concurrently.

        Args:
            requests: List of download requests.
            concurrency: Maximum number of concurrent downloads (uses config if None).

        Returns:
            BatchDownloadResponse with all results.
        """
        if not requests:
            return BatchDownloadResponse(
                total=0,
                success=0,
                failed=0,
                results=[],
            )

        # Use config value if not specified
        if concurrency is None:
            config = await self._get_system_config()
            concurrency = config.concurrent_downloads

        # Create semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrency)

        async def download_with_semaphore(
            request: ImageDownloadRequest,
        ) -> ImageDownloadResult:
            async with semaphore:
                return await self.download_image(
                    url=request.url,
                    save_path=request.save_path,
                    filename=request.filename,
                )

        # Execute all downloads concurrently
        tasks = [download_with_semaphore(req) for req in requests]
        results = await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        return BatchDownloadResponse(
            total=len(results),
            success=success_count,
            failed=failed_count,
            results=list(results),
        )

    def generate_series_image_requests(
        self,
        save_path: str,
        poster_path: str | None = None,
        backdrop_path: str | None = None,
        size: ImageSize = ImageSize.W500,
    ) -> list[ImageDownloadRequest]:
        """
        Generate download requests for series images.

        Args:
            save_path: Directory to save images.
            poster_path: TMDB poster path.
            backdrop_path: TMDB backdrop path.
            size: Image size.

        Returns:
            List of ImageDownloadRequest objects.
        """
        requests = []

        if poster_path:
            url = self.get_full_image_url(poster_path, size)
            if url:
                requests.append(
                    ImageDownloadRequest(
                        url=url,
                        save_path=save_path,
                        filename="poster.jpg",
                    )
                )

        if backdrop_path:
            # Backdrop usually needs larger size
            backdrop_size = ImageSize.W780 if size != ImageSize.ORIGINAL else size
            url = self.get_full_image_url(backdrop_path, backdrop_size)
            if url:
                requests.append(
                    ImageDownloadRequest(
                        url=url,
                        save_path=save_path,
                        filename="backdrop.jpg",
                    )
                )

        return requests

    def generate_season_image_request(
        self,
        save_path: str,
        season_number: int,
        poster_path: str | None,
        size: ImageSize = ImageSize.W500,
    ) -> ImageDownloadRequest | None:
        """
        Generate download request for season poster.

        Args:
            save_path: Directory to save image.
            season_number: Season number for filename.
            poster_path: TMDB poster path.
            size: Image size.

        Returns:
            ImageDownloadRequest or None if no poster.
        """
        if not poster_path:
            return None

        url = self.get_full_image_url(poster_path, size)
        if not url:
            return None

        return ImageDownloadRequest(
            url=url,
            save_path=save_path,
            filename=f"season{season_number:02d}-poster.jpg",
        )

    def generate_episode_image_request(
        self,
        save_path: str,
        season_number: int,
        episode_number: int,
        still_path: str | None,
        size: ImageSize = ImageSize.W500,
    ) -> ImageDownloadRequest | None:
        """
        Generate download request for episode thumbnail.

        Args:
            save_path: Directory to save image (Season folder).
            season_number: Season number for filename.
            episode_number: Episode number for filename.
            still_path: TMDB still image path.
            size: Image size.

        Returns:
            ImageDownloadRequest or None if no still.
        """
        if not still_path:
            return None

        url = self.get_full_image_url(still_path, size)
        if not url:
            return None

        return ImageDownloadRequest(
            url=url,
            save_path=save_path,
            filename=f"S{season_number:02d}E{episode_number:02d}.jpg",
        )
