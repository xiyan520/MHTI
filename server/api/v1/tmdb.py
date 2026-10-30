"""TMDB API endpoints for search and metadata retrieval.

异常处理：所有 TMDBError 子类（TMDBNotConfiguredError、TMDBTimeoutError、
TMDBConnectionError、TMDBNotFoundError）由全局异常处理器统一处理。
"""

from fastapi import APIRouter, Depends, Query

from server.api.deps import get_tmdb_service, require_auth
from server.domain.metadata.tmdb_service import TMDBService
from server.models.tmdb import (
    TMDBError,
    TMDBSearchResponse,
    TMDBSeason,
    TMDBSeries,
)

router = APIRouter(prefix="/api/tmdb", tags=["tmdb"], dependencies=[Depends(require_auth)])


@router.get(
    "/search",
    response_model=TMDBSearchResponse,
    responses={
        400: {"model": TMDBError, "description": "API Token not configured"},
        408: {"model": TMDBError, "description": "Request timeout"},
        502: {"model": TMDBError, "description": "TMDB connection error"},
    },
)
async def search_tv(
    q: str = Query(..., min_length=1, description="Search query"),
    language: str = Query("zh-CN", description="Language for results"),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> TMDBSearchResponse:
    """
    Search for TV series on TMDB.

    Args:
        q: Search query string
        language: Language for results (default: zh-CN)

    Returns:
        Search results with matching TV series.

    Raises:
        TMDBNotConfiguredError: API Token 未配置 (400)
        TMDBTimeoutError: 请求超时 (408)
        TMDBConnectionError: 连接失败 (502)
    """
    return await tmdb_service.search_series_by_api(query=q, language=language)


@router.get(
    "/series/{tmdb_id}",
    response_model=TMDBSeries,
    responses={
        404: {"model": TMDBError, "description": "Series not found"},
        408: {"model": TMDBError, "description": "Request timeout"},
        502: {"model": TMDBError, "description": "TMDB connection error"},
    },
)
async def get_series(
    tmdb_id: int,
    language: str = Query("zh-CN", description="Language for metadata"),
    include_episodes: bool = Query(True, description="Include episode details for each season"),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> TMDBSeries:
    """
    Get TV series details from TMDB.

    Args:
        tmdb_id: TMDB series ID
        language: Language for metadata (default: zh-CN)
        include_episodes: Whether to include episode details (default: True)

    Returns:
        Complete series information including seasons and episodes.

    Raises:
        TMDBNotFoundError: 剧集未找到 (404)
        TMDBTimeoutError: 请求超时 (408)
        TMDBConnectionError: 连接失败 (502)
    """
    return await tmdb_service.get_series_with_episodes_or_raise(
        tmdb_id=tmdb_id,
        language=language,
        include_episodes=include_episodes,
    )


@router.get(
    "/series/{tmdb_id}/season/{season_number}",
    response_model=TMDBSeason,
    responses={
        404: {"model": TMDBError, "description": "Season not found"},
        408: {"model": TMDBError, "description": "Request timeout"},
        502: {"model": TMDBError, "description": "TMDB connection error"},
    },
)
async def get_season(
    tmdb_id: int,
    season_number: int,
    language: str = Query("zh-CN", description="Language for metadata"),
    tmdb_service: TMDBService = Depends(get_tmdb_service),
) -> TMDBSeason:
    """
    Get season details including episodes.

    Args:
        tmdb_id: TMDB series ID
        season_number: Season number
        language: Language for metadata (default: zh-CN)

    Returns:
        Season information with episode list.

    Raises:
        TMDBNotFoundError: 季未找到 (404)
        TMDBTimeoutError: 请求超时 (408)
        TMDBConnectionError: 连接失败 (502)
    """
    return await tmdb_service.get_season_or_raise(
        tmdb_id=tmdb_id,
        season_number=season_number,
        language=language,
    )
