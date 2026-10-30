"""TMDB data models."""

from datetime import date

from pydantic import BaseModel


class TMDBSearchResult(BaseModel):
    """Single search result item."""

    id: int
    name: str
    original_name: str | None = None
    first_air_date: date | None = None
    poster_path: str | None = None
    overview: str | None = None
    vote_average: float | None = None
    adult: bool = False
    # 详情信息（可选，需要额外 API 调用获取）
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None


class TMDBSearchResponse(BaseModel):
    """Search response containing results."""

    query: str
    total_results: int
    results: list[TMDBSearchResult]


class TMDBEpisode(BaseModel):
    """Episode information."""

    episode_number: int
    name: str
    overview: str | None = None
    air_date: date | None = None
    vote_average: float | None = None
    still_path: str | None = None


class TMDBSeason(BaseModel):
    """Season information."""

    season_number: int
    name: str
    overview: str | None = None
    air_date: date | None = None
    poster_path: str | None = None
    episode_count: int | None = None
    episodes: list[TMDBEpisode] | None = None


class TMDBSeries(BaseModel):
    """Complete series information."""

    id: int
    name: str
    original_name: str | None = None
    overview: str | None = None
    first_air_date: date | None = None
    vote_average: float | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    genres: list[str] = []
    status: str | None = None
    number_of_seasons: int | None = None
    number_of_episodes: int | None = None
    seasons: list[TMDBSeason] = []


class TMDBError(BaseModel):
    """Error response."""

    error: str
    message: str
