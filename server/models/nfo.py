"""NFO data models for Jellyfin/Emby compatibility."""

from datetime import date

from pydantic import BaseModel


class TVShowNFO(BaseModel):
    """TVShow NFO data model."""

    title: str
    original_title: str | None = None
    sort_title: str | None = None
    rating: float | None = None
    year: int | None = None
    plot: str | None = None
    tmdb_id: int | None = None
    genres: list[str] = []
    premiered: date | None = None
    status: str | None = None


class SeasonNFO(BaseModel):
    """Season NFO data model."""

    season_number: int
    title: str | None = None
    plot: str | None = None
    premiered: date | None = None


class EpisodeNFO(BaseModel):
    """Episode NFO data model."""

    title: str
    season: int
    episode: int
    plot: str | None = None
    aired: date | None = None
    rating: float | None = None


class NFOResponse(BaseModel):
    """NFO generation response."""

    nfo: str
    filename: str


# ========== NFO 配置模型 ==========


class TVShowNfoFields(BaseModel):
    """剧集 NFO 字段配置"""

    enabled: bool = True  # 生成 tvshow.nfo
    title: bool = True  # 标题
    originaltitle: bool = True  # 原标题
    sorttitle: bool = True  # 排序标题
    plot: bool = True  # 简介
    outline: bool = True  # 简介摘要
    year: bool = True  # 年份
    premiered: bool = True  # 首播日期
    rating: bool = True  # 评分
    genre: bool = True  # 类型
    status: bool = True  # 状态
    tmdbid: bool = True  # TMDB ID


class SeasonNfoFields(BaseModel):
    """季 NFO 字段配置"""

    enabled: bool = True  # 生成 season.nfo
    title: bool = True  # 标题
    plot: bool = True  # 简介
    year: bool = True  # 年份
    premiered: bool = True  # 首播日期
    seasonnumber: bool = True  # 季号


class EpisodeNfoFields(BaseModel):
    """集 NFO 字段配置"""

    enabled: bool = True  # 生成 episode.nfo
    title: bool = True  # 标题
    plot: bool = True  # 简介
    season: bool = True  # 季号
    episode: bool = True  # 集号
    aired: bool = True  # 播出日期
    rating: bool = True  # 评分


class NfoConfig(BaseModel):
    """NFO 配置模型 - 剧集刮削器"""

    # 总开关
    enabled: bool = True

    # 各级别 NFO 配置
    tvshow: TVShowNfoFields = TVShowNfoFields()
    season: SeasonNfoFields = SeasonNfoFields()
    episode: EpisodeNfoFields = EpisodeNfoFields()
