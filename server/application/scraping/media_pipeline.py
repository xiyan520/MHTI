"""Scraper 媒体文件处理 - 图片下载、字幕处理与 Emby 冲突检测。"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from server.models.emby import ConflictCheckRequest, ConflictCheckResult, ConflictType
from server.models.tmdb import TMDBSeason, TMDBSeries

if TYPE_CHECKING:
    from server.domain.artifacts.image_service import ImageService
    from server.domain.artifacts.subtitle_service import SubtitleService
    from server.domain.integration.emby_service import EmbyService

logger = logging.getLogger(__name__)


class ScraperMediaPipeline:
    """媒体文件处理，提供图片下载、字幕处理和 Emby 冲突检测方法。"""

    def __init__(
        self,
        image_service: ImageService,
        subtitle_service: SubtitleService,
        emby_service: EmbyService,
    ) -> None:
        self.image_service = image_service
        self.subtitle_service = subtitle_service
        self.emby_service = emby_service

    async def download_series_images(
        self,
        series: TMDBSeries,
        series_folder: str,
        download_poster: bool = True,
        download_fanart: bool = True,
    ) -> None:
        """下载剧集海报和背景图。

        Args:
            series: TMDBSeries 对象，包含图片路径。
            series_folder: 剧集文件夹路径。
            download_poster: 是否下载海报图。
            download_fanart: 是否下载背景图。
        """
        folder = Path(series_folder)

        # 检查图片是否已存在，避免重复下载
        poster_path = folder / "poster.jpg"
        backdrop_path = folder / "backdrop.jpg"

        # 根据配置和文件存在情况决定是否需要下载
        need_poster = download_poster and not poster_path.exists()
        need_backdrop = download_fanart and not backdrop_path.exists()

        if not need_poster and not need_backdrop:
            logger.info("剧集图片已存在或配置禁用，跳过下载")
            return

        # 生成下载请求
        requests = self.image_service.generate_series_image_requests(
            save_path=series_folder,
            poster_path=series.poster_path if need_poster else None,
            backdrop_path=series.backdrop_path if need_backdrop else None,
        )

        if not requests:
            logger.info("没有可下载的剧集图片")
            return

        # 过滤已存在的图片
        filtered_requests = []
        for req in requests:
            target_path = Path(req.save_path) / req.filename
            if not target_path.exists():
                filtered_requests.append(req)

        if not filtered_requests:
            logger.info("剧集图片已存在，跳过下载")
            return

        # 下载图片
        logger.info(f"开始下载剧集图片: {len(filtered_requests)} 个")
        result = await self.image_service.download_batch(filtered_requests)
        logger.info(f"图片下载完成: 成功 {result.success}, 失败 {result.failed}")

    async def download_episode_image(
        self,
        season_info: TMDBSeason | None,
        season_num: int,
        episode_num: int,
        season_folder: str,
        video_stem: str,
    ) -> None:
        """下载集封面图，使用与视频文件相同的文件名。

        Args:
            season_info: TMDBSeason 对象，包含剧集信息。
            season_num: 季号。
            episode_num: 集号。
            season_folder: 季度文件夹路径。
            video_stem: 视频文件名（不含扩展名）。
        """
        if not season_info or not season_info.episodes:
            logger.info("没有季度信息，跳过集封面图下载")
            return

        # 查找当前集的 still_path
        still_path = None
        for ep in season_info.episodes:
            if ep.episode_number == episode_num:
                still_path = ep.still_path
                break

        if not still_path:
            logger.info(f"S{season_num:02d}E{episode_num:02d} 没有封面图")
            return

        # 使用与视频文件相同的文件名
        target_filename = f"{video_stem}.jpg"
        target_path = Path(season_folder) / target_filename
        if target_path.exists():
            logger.info(f"集封面图已存在: {target_filename}")
            return

        # 获取图片 URL
        url = self.image_service.get_full_image_url(still_path)
        if not url:
            return

        # 下载图片
        logger.info(f"开始下载集封面图: {target_filename}")
        result = await self.image_service.download_image(
            url=url,
            save_path=season_folder,
            filename=target_filename,
        )
        if result.success:
            logger.info(f"集封面图下载成功: {target_filename}")
        else:
            logger.warning(f"集封面图下载失败: {result.error}")

    def process_subtitles(
        self,
        source_video_path: str,
        dest_video_path: str,
    ) -> list[str]:
        """查找并移动与视频关联的字幕文件。

        Args:
            source_video_path: 原视频文件路径。
            dest_video_path: 目标视频文件路径。

        Returns:
            已移动的字幕文件路径列表。
        """
        source_path = Path(source_video_path)
        dest_path = Path(dest_video_path)
        source_folder = source_path.parent
        dest_folder = dest_path.parent
        source_stem = source_path.stem
        dest_stem = dest_path.stem

        moved_subtitles = []

        # 扫描源文件夹中的字幕
        scan_result = self.subtitle_service.scan_subtitles(str(source_folder))
        if not scan_result.subtitles:
            logger.info("未找到关联字幕文件")
            return moved_subtitles

        # 查找匹配的字幕
        for sub in scan_result.subtitles:
            sub_base = self.subtitle_service._get_base_name(sub.filename)
            if self.subtitle_service._names_match(source_stem, sub_base):
                # 重命名并移动字幕
                result = self.subtitle_service.rename_subtitle(
                    subtitle_path=sub.path,
                    new_video_name=dest_stem,
                    preserve_language=True,
                )
                if result.success:
                    # 如果目标文件夹不同，移动到目标文件夹
                    renamed_path = Path(result.dest_path)
                    if renamed_path.parent != dest_folder:
                        final_path = dest_folder / renamed_path.name
                        try:
                            shutil.move(str(renamed_path), str(final_path))
                            moved_subtitles.append(str(final_path))
                            logger.info(f"字幕已移动: {renamed_path.name} -> {final_path}")
                        except OSError as e:
                            logger.warning(f"字幕移动失败: {e}")
                    else:
                        moved_subtitles.append(result.dest_path)
                        logger.info(f"字幕已重命名: {sub.filename} -> {renamed_path.name}")
                else:
                    logger.warning(f"字幕处理失败: {result.error}")

        return moved_subtitles

    async def check_emby_conflict(
        self,
        series_name: str,
        tmdb_id: int | None,
        season: int,
        episode: int,
    ) -> ConflictCheckResult:
        """检查 Emby 媒体库冲突。

        Args:
            series_name: 剧集名称。
            tmdb_id: TMDB ID。
            season: 季号。
            episode: 集号。

        Returns:
            冲突检测结果。
        """
        config = await self.emby_service.get_config()

        if not config.enabled or not config.check_before_scrape:
            return ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)

        return await self.emby_service.check_conflict(
            ConflictCheckRequest(
                series_name=series_name,
                tmdb_id=tmdb_id,
                season=season,
                episode=episode,
            )
        )
