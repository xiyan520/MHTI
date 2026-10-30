"""Scraper data models."""

from enum import Enum

from pydantic import BaseModel

from server.models.emby import ConflictCheckResult
from server.models.history import ScrapeLogStep
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.storage import StorageLocator
from server.models.tmdb import TMDBEpisode, TMDBSearchResult, TMDBSeries


class ScrapeStatus(str, Enum):
    """Scrape operation status."""

    SUCCESS = "success"
    SEARCH_FAILED = "search_failed"
    API_FAILED = "api_failed"
    MOVE_FAILED = "move_failed"
    NFO_FAILED = "nfo_failed"
    NO_MATCH = "no_match"
    NEED_SELECTION = "need_selection"  # 需要用户选择剧集
    NEED_SEASON_EPISODE = "need_season_episode"  # 需要用户输入季/集
    FILE_CONFLICT = "file_conflict"  # 目标文件已存在
    EMBY_CONFLICT = "emby_conflict"  # Emby 媒体库冲突


class ScrapeRequest(BaseModel):
    """Request for scraping a single file."""

    file_path: str
    output_dir: str | None = None  # 视频输出目录
    metadata_dir: str | None = None  # 元数据输出目录（NFO、图片）
    file_locator: StorageLocator | None = None
    output_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: OrganizeMode | None = None  # 整理模式
    auto_select: bool = True  # 自动选择最佳匹配
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置


class ScrapeByIdRequest(BaseModel):
    """Request for scraping with manual TMDB ID."""

    file_path: str
    tmdb_id: int
    season: int
    episode: int
    output_dir: str | None = None  # 视频输出目录
    metadata_dir: str | None = None  # 元数据输出目录（NFO、图片）
    file_locator: StorageLocator | None = None
    output_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: OrganizeMode | None = None  # 整理模式
    skip_emby_check: bool = False  # 跳过 Emby 冲突检查
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置


class ScrapeResult(BaseModel):
    """Result of a scrape operation."""

    file_path: str
    status: ScrapeStatus
    message: str | None = None
    # 解析结果
    parsed_title: str | None = None
    parsed_season: int | None = None
    parsed_episode: int | None = None
    # 搜索结果
    search_results: list[TMDBSearchResult] | None = None
    selected_id: int | None = None
    # 元数据
    series_info: TMDBSeries | None = None
    episode_info: TMDBEpisode | None = None  # 集信息
    # 移动结果
    dest_path: str | None = None
    nfo_path: str | None = None
    # Emby 冲突
    emby_conflict: ConflictCheckResult | None = None
    # 刮削日志
    scrape_logs: list[ScrapeLogStep] = []


class BatchScrapeRequest(BaseModel):
    """Request for batch scraping."""

    file_paths: list[str]
    output_dir: str | None = None
    auto_select: bool = True
    dry_run: bool = False  # 预览模式，不实际执行


class BatchScrapeResponse(BaseModel):
    """Response for batch scraping."""

    total: int
    success: int
    failed: int
    results: list[ScrapeResult]


class ScrapePreview(BaseModel):
    """Preview of scrape operation."""

    file_path: str
    parsed_title: str | None = None
    parsed_season: int | None = None
    parsed_episode: int | None = None
    search_results: list[TMDBSearchResult] | None = None
    suggested_dest: str | None = None
