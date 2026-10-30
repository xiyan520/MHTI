"""Scraped file models - 已刮削文件记录模型"""

from datetime import datetime

from pydantic import BaseModel


class ScrapedFile(BaseModel):
    """已刮削文件记录"""
    id: str
    source_path: str  # 原始文件路径
    target_path: str | None = None  # 目标文件路径
    file_size: int  # 文件大小
    tmdb_id: int | None = None  # TMDB ID
    season: int | None = None  # 季号
    episode: int | None = None  # 集号
    title: str | None = None  # 剧集标题
    scraped_at: datetime  # 刮削时间
    history_record_id: str | None = None  # 关联的历史记录ID


class ScrapedFileCreate(BaseModel):
    """创建已刮削文件记录"""
    source_path: str
    target_path: str | None = None
    file_size: int
    tmdb_id: int | None = None
    season: int | None = None
    episode: int | None = None
    title: str | None = None
    history_record_id: str | None = None
