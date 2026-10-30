"""Image download API endpoints."""

from fastapi import APIRouter, Depends

from server.api.deps import get_image_service, require_auth
from server.domain.artifacts.image_service import ImageService
from server.models.image import (
    BatchDownloadRequest,
    BatchDownloadResponse,
    ImageDownloadRequest,
    ImageDownloadResult,
)

router = APIRouter(prefix="/api/images", tags=["images"], dependencies=[Depends(require_auth)])


@router.post("/download", response_model=ImageDownloadResult)
async def download_image(
    request: ImageDownloadRequest,
    image_service: ImageService = Depends(get_image_service),
) -> ImageDownloadResult:
    """
    Download a single image.

    Args:
        request: Image download request with URL and save path.

    Returns:
        Download result with success status.
    """
    return await image_service.download_image(
        url=request.url,
        save_path=request.save_path,
        filename=request.filename,
    )


@router.post("/download/batch", response_model=BatchDownloadResponse)
async def download_batch(
    request: BatchDownloadRequest,
    image_service: ImageService = Depends(get_image_service),
) -> BatchDownloadResponse:
    """
    Download multiple images concurrently.

    Args:
        request: Batch download request with image list and concurrency.

    Returns:
        Batch download response with all results.
    """
    return await image_service.download_batch(
        requests=request.images,
        concurrency=request.concurrency,
    )
