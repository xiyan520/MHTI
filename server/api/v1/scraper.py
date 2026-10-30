"""Scraper API endpoints for file scraping operations.

异常处理：所有 TMDBError 子类（TMDBNotConfiguredError、TMDBInvalidCredentialsError）
由全局异常处理器统一处理。
"""

from fastapi import APIRouter, Depends

from server.api.deps import get_scraper_service, require_auth
from server.application.scraping.service import ScraperService
from server.models.scraper import (
    BatchScrapeRequest,
    BatchScrapeResponse,
    ScrapeByIdRequest,
    ScrapePreview,
    ScrapeRequest,
    ScrapeResult,
)

router = APIRouter(prefix="/api/scraper", tags=["scraper"], dependencies=[Depends(require_auth)])


@router.get("/status")
async def get_scraper_status(
    scraper_service: ScraperService = Depends(get_scraper_service),
) -> dict:
    """
    Check if scraper is ready (Cookie and API Token configured).

    Returns:
        Status dict with ready flag and message.
    """
    is_ready, error = await scraper_service.check_config()
    return {
        "ready": is_ready,
        "message": error if not is_ready else "刮削服务就绪",
    }


@router.post("/preview", response_model=ScrapePreview)
async def preview_scrape(
    request: ScrapeRequest,
    scraper_service: ScraperService = Depends(get_scraper_service),
) -> ScrapePreview:
    """
    Preview scrape operation without executing.

    Parses filename and searches TMDB, but does not move files or generate NFO.

    Args:
        request: Scrape request with file path.

    Returns:
        Preview with parsed info and search results.
    """
    return await scraper_service.preview(request.file_path)


@router.post("/file", response_model=ScrapeResult)
async def scrape_file(
    request: ScrapeRequest,
    scraper_service: ScraperService = Depends(get_scraper_service),
) -> ScrapeResult:
    """
    Scrape a single video file.

    Complete workflow:
    1. Parse filename
    2. Search TMDB (Cookie)
    3. Auto-select match
    4. Get details (API)
    5. Generate NFO
    6. Move file

    Args:
        request: Scrape request with file path and options.

    Returns:
        ScrapeResult with operation status.

    Raises:
        TMDBNotConfiguredError: Cookie 或 API Token 未配置 (400)
        TMDBInvalidCredentialsError: Cookie 或 API Token 已失效 (401)
    """
    await scraper_service.ensure_config_ready()
    return await scraper_service.scrape_file(request)


@router.post("/file/by-id", response_model=ScrapeResult)
async def scrape_file_by_id(
    request: ScrapeByIdRequest,
    scraper_service: ScraperService = Depends(get_scraper_service),
) -> ScrapeResult:
    """
    Scrape a file with manually specified TMDB ID.

    Use this when automatic search fails.

    Args:
        request: Request with file path and TMDB ID.

    Returns:
        ScrapeResult with operation status.

    Raises:
        TMDBNotConfiguredError: API Token 未配置 (400)
        TMDBInvalidCredentialsError: API Token 已失效 (401)
    """
    await scraper_service.ensure_api_token_ready()
    return await scraper_service.scrape_by_id(request)


@router.post("/batch", response_model=BatchScrapeResponse)
async def batch_scrape(
    request: BatchScrapeRequest,
    scraper_service: ScraperService = Depends(get_scraper_service),
) -> BatchScrapeResponse:
    """
    Batch scrape multiple video files.

    Args:
        request: Batch request with file paths and options.
        - dry_run: If true, only preview without executing.

    Returns:
        BatchScrapeResponse with all results.

    Raises:
        TMDBNotConfiguredError: Cookie 或 API Token 未配置 (400)
        TMDBInvalidCredentialsError: Cookie 或 API Token 已失效 (401)
    """
    if not request.dry_run:
        await scraper_service.ensure_config_ready()

    return await scraper_service.batch_scrape(request)
