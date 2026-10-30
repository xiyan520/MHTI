"""Download configuration data models."""

from enum import Enum

from pydantic import BaseModel


class ImageQuality(str, Enum):
    """图片质量枚举"""

    ORIGINAL = "original"  # 原始尺寸
    HIGH = "w1280"  # 高清 1280px
    MEDIUM = "w780"  # 中等 780px
    LOW = "w500"  # 低质量 500px
    THUMB = "w300"  # 缩略图 300px


class DownloadConfig(BaseModel):
    """下载配置模型 - 剧集刮削器"""

    # ===== 剧集级别图片 (TV Show) =====
    series_poster: bool = True  # 剧集海报 (poster.jpg)
    series_backdrop: bool = True  # 剧集背景图 (fanart.jpg)
    series_logo: bool = False  # 剧集 Logo (logo.png)
    series_banner: bool = False  # 剧集横幅 (banner.jpg)

    # ===== 季级别图片 (Season) =====
    season_poster: bool = True  # 季海报 (season01-poster.jpg)

    # ===== 集级别图片 (Episode) =====
    episode_thumb: bool = True  # 剧集截图/缩略图 (S01E01-thumb.jpg)

    # ===== 额外图片 =====
    extra_backdrops: bool = False  # 额外背景图 (extrafanart/)
    extra_backdrops_count: int = 5  # 额外背景图数量上限

    # ===== 图片质量设置 =====
    poster_quality: ImageQuality = ImageQuality.HIGH  # 海报质量
    backdrop_quality: ImageQuality = ImageQuality.ORIGINAL  # 背景图质量
    thumb_quality: ImageQuality = ImageQuality.MEDIUM  # 缩略图质量

    # ===== 下载行为 =====
    overwrite_existing: bool = False  # 覆盖已存在的图片
