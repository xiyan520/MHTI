"""NFO generation API endpoints."""

from fastapi import APIRouter, Depends

from server.api.deps import get_nfo_service, require_auth
from server.domain.artifacts.nfo_service import NFOService
from server.models.nfo import EpisodeNFO, NFOResponse, SeasonNFO, TVShowNFO

router = APIRouter(prefix="/api/nfo", tags=["nfo"], dependencies=[Depends(require_auth)])


@router.post("/tvshow", response_model=NFOResponse)
async def generate_tvshow_nfo(
    data: TVShowNFO,
    nfo_service: NFOService = Depends(get_nfo_service),
) -> NFOResponse:
    """
    Generate tvshow.nfo XML content.

    Args:
        data: TVShow NFO data.

    Returns:
        NFO XML string and suggested filename.
    """
    nfo_content = nfo_service.generate_tvshow_nfo(data)
    return NFOResponse(nfo=nfo_content, filename="tvshow.nfo")


@router.post("/season", response_model=NFOResponse)
async def generate_season_nfo(
    data: SeasonNFO,
    nfo_service: NFOService = Depends(get_nfo_service),
) -> NFOResponse:
    """
    Generate season.nfo XML content.

    Args:
        data: Season NFO data.

    Returns:
        NFO XML string and suggested filename.
    """
    nfo_content = nfo_service.generate_season_nfo(data)
    return NFOResponse(nfo=nfo_content, filename="season.nfo")


@router.post("/episode", response_model=NFOResponse)
async def generate_episode_nfo(
    data: EpisodeNFO,
    nfo_service: NFOService = Depends(get_nfo_service),
) -> NFOResponse:
    """
    Generate episode.nfo XML content.

    Args:
        data: Episode NFO data.

    Returns:
        NFO XML string and suggested filename.
    """
    nfo_content = nfo_service.generate_episode_nfo(data)
    # Suggest filename based on season and episode
    filename = f"S{data.season:02d}E{data.episode:02d}.nfo"
    return NFOResponse(nfo=nfo_content, filename=filename)
