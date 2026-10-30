"""Scraper 配置解析 - 配置检查与有效配置获取。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.models.manual_job import ManualJobAdvancedSettings

if TYPE_CHECKING:
    from server.domain.metadata.tmdb_service import TMDBService
    from server.domain.system.config_service import ConfigService


class ScraperConfigResolver:
    """配置管理，提供配置检查和获取方法。"""

    def __init__(self, config_service: ConfigService, tmdb_service: TMDBService) -> None:
        self.config_service = config_service
        self.tmdb_service = tmdb_service

    async def get_effective_download_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None
    ) -> dict:
        """获取有效的下载配置（任务设置优先于全局配置）。

        Args:
            advanced_settings: 可选的高级设置。

        Returns:
            包含 download_poster, download_thumb, download_fanart 的配置字典。
        """
        # 如果没有高级设置或使用全局配置
        if advanced_settings is None or advanced_settings.use_global_download:
            global_config = await self.config_service.get_download_config()
            return {
                "download_poster": global_config.series_poster,
                "download_thumb": global_config.episode_thumb,
                "download_fanart": global_config.series_backdrop,
            }
        else:
            # 使用任务级设置
            return {
                "download_poster": advanced_settings.download_poster,
                "download_thumb": advanced_settings.download_thumb,
                "download_fanart": advanced_settings.download_fanart,
            }

    async def get_effective_nfo_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None
    ) -> dict:
        """获取有效的 NFO 配置。

        Args:
            advanced_settings: 可选的高级设置。

        Returns:
            包含 nfo_enabled 的配置字典。
        """
        if advanced_settings is None or advanced_settings.use_global_metadata:
            global_config = await self.config_service.get_nfo_config()
            return {
                "nfo_enabled": global_config.enabled,
            }
        else:
            return {
                "nfo_enabled": advanced_settings.nfo_enabled,
            }

    async def check_config(self) -> tuple[bool, str | None]:
        """检查必要配置是否已就绪。

        Returns:
            (is_ready, error_message) 元组。
        """
        # Check Cookie
        cookie_status = await self.tmdb_service.get_cookie_status()
        if not cookie_status.is_configured:
            return False, "请先配置 TMDB Cookie"
        if cookie_status.is_valid is False:
            return False, "TMDB Cookie 已失效，请更新"

        # Check API Token
        token_status = await self.tmdb_service.get_api_token_status()
        if not token_status.is_configured:
            return False, "请先配置 TMDB API Token"
        if token_status.is_valid is False:
            return False, "TMDB API Token 已失效，请更新"

        return True, None

    async def ensure_config_ready(self) -> None:
        """确保必要配置已就绪。

        Raises:
            TMDBNotConfiguredError: Cookie 或 API Token 未配置。
            TMDBInvalidCredentialsError: Cookie 或 API Token 已失效。
        """
        from server.common.exceptions import (
            TMDBInvalidCredentialsError,
            TMDBNotConfiguredError,
        )

        # Check Cookie
        cookie_status = await self.tmdb_service.get_cookie_status()
        if not cookie_status.is_configured:
            raise TMDBNotConfiguredError("Cookie")
        if cookie_status.is_valid is False:
            raise TMDBInvalidCredentialsError("Cookie")

        # Check API Token
        token_status = await self.tmdb_service.get_api_token_status()
        if not token_status.is_configured:
            raise TMDBNotConfiguredError("API Token")
        if token_status.is_valid is False:
            raise TMDBInvalidCredentialsError("API Token")

    async def ensure_api_token_ready(self) -> None:
        """确保 API Token 已配置且有效。

        Raises:
            TMDBNotConfiguredError: API Token 未配置。
            TMDBInvalidCredentialsError: API Token 已失效。
        """
        from server.common.exceptions import (
            TMDBInvalidCredentialsError,
            TMDBNotConfiguredError,
        )

        token_status = await self.tmdb_service.get_api_token_status()
        if not token_status.is_configured:
            raise TMDBNotConfiguredError("API Token")
        if token_status.is_valid is False:
            raise TMDBInvalidCredentialsError("API Token")
