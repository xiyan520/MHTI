"""Configuration storage service."""

import json
import logging
from datetime import datetime
from pathlib import Path

from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.config_repository import ConfigRepository
from server.infrastructure.security import decrypt, encrypt
from server.models.cloud_115 import Cloud115Config
from server.models.config import ApiTokenStatus, LanguageConfig, ProxyConfig, ProxyType
from server.models.download import DownloadConfig
from server.models.emby import EmbyConfig
from server.models.nfo import NfoConfig
from server.models.organize import OrganizeConfig, OrganizeMode
from server.models.system import SystemConfig
from server.models.template import NamingTemplate
from server.models.watcher import WatcherConfig, WatcherMode

logger = logging.getLogger(__name__)

# Config keys
COOKIE_KEY = "tmdb_cookie"
COOKIE_VERIFIED_KEY = "tmdb_cookie_verified"
COOKIE_VERIFIED_AT_KEY = "tmdb_cookie_verified_at"
PROXY_CONFIG_KEY = "proxy_config"
LANGUAGE_CONFIG_KEY = "language_config"
API_TOKEN_KEY = "tmdb_api_token"
API_TOKEN_VERIFIED_KEY = "tmdb_api_token_verified"
API_TOKEN_VERIFIED_AT_KEY = "tmdb_api_token_verified_at"
ADULT_ENABLED_KEY = "tmdb_adult_enabled"
ADULT_MESSAGE_KEY = "tmdb_adult_message"
ADULT_CHECKED_AT_KEY = "tmdb_adult_checked_at"
ORGANIZE_CONFIG_KEY = "organize_config"
DOWNLOAD_CONFIG_KEY = "download_config"
WATCHER_CONFIG_KEY = "watcher_config"
NFO_CONFIG_KEY = "nfo_config"
SYSTEM_CONFIG_KEY = "system_config"
EMBY_CONFIG_KEY = "emby_config"
NAMING_CONFIG_KEY = "naming_config"
# 「旧默认 30s 已迁移」标记：只迁移一次，之后用户主动改回 30 也不会再被改
TASK_TIMEOUT_MIGRATED_KEY = "system_config_task_timeout_migrated"
CLOUD_115_CONFIG_KEY = "cloud_115_config"


class ConfigService:
    """Service for managing application configuration."""

    def __init__(self, db_path: Path | None = None):
        """Initialize config service."""
        # db_path 保留为公共属性（外部直接读库）；仓储以 None 走连接池，显式路径直连
        self.db_path = db_path or DATABASE_PATH
        self._repo = ConfigRepository(db_path)

    async def _ensure_db(self) -> None:
        """确保独立库场景下建库建表（测试直接调用）。"""
        await self._repo.ensure_schema()

    async def get(self, key: str, encrypted: bool = False) -> str | None:
        """Get a configuration value."""
        row = await self._repo.get_value(key)
        if row is None:
            return None
        value, is_encrypted = row
        if is_encrypted and encrypted:
            return decrypt(value)
        return value

    async def set(self, key: str, value: str, encrypted: bool = False) -> None:
        """Set a configuration value."""
        stored_value = encrypt(value) if encrypted else value
        await self._repo.set_value(key, stored_value, encrypted, datetime.now().isoformat())

    async def delete(self, key: str) -> bool:
        """Delete a configuration value."""
        return await self._repo.delete_value(key)

    async def exists(self, key: str) -> bool:
        """Check if a configuration key exists."""
        return await self._repo.exists(key)

    async def delete_by_prefix(self, prefix: str) -> int:
        """按前缀批量删除配置项（115 登录中间态清理等）。"""
        return await self._repo.delete_by_prefix(prefix)

    # Cookie-specific methods
    async def save_cookie(self, cookie: str) -> None:
        """Save TMDB cookie (encrypted)."""
        await self.set(COOKIE_KEY, cookie, encrypted=True)

    async def get_cookie(self) -> str | None:
        """Get TMDB cookie (decrypted)."""
        return await self.get(COOKIE_KEY, encrypted=True)

    async def delete_cookie(self) -> bool:
        """Delete TMDB cookie and related data."""
        deleted = await self.delete(COOKIE_KEY)
        await self.delete(COOKIE_VERIFIED_KEY)
        await self.delete(COOKIE_VERIFIED_AT_KEY)
        return deleted

    async def has_cookie(self) -> bool:
        """Check if cookie is configured."""
        return await self.exists(COOKIE_KEY)

    async def set_cookie_verified(self, is_valid: bool) -> None:
        """Set cookie verification status."""
        await self.set(COOKIE_VERIFIED_KEY, "1" if is_valid else "0")
        await self.set(COOKIE_VERIFIED_AT_KEY, datetime.now().isoformat())

    async def get_cookie_verification(self) -> tuple[bool | None, datetime | None]:
        """Get cookie verification status and timestamp."""
        is_valid_str = await self.get(COOKIE_VERIFIED_KEY)
        verified_at_str = await self.get(COOKIE_VERIFIED_AT_KEY)

        is_valid = None
        if is_valid_str is not None:
            is_valid = is_valid_str == "1"

        verified_at = None
        if verified_at_str:
            try:
                verified_at = datetime.fromisoformat(verified_at_str)
            except ValueError:
                pass

        return is_valid, verified_at

    # Proxy-specific methods
    async def save_proxy_config(self, config: ProxyConfig) -> None:
        """Save proxy configuration."""
        data = {
            "type": config.type.value,
            "host": config.host,
            "port": config.port,
            "username": config.username,
            "password": config.password,
        }
        # Encrypt if has credentials
        encrypted = bool(config.username and config.password)
        await self.set(PROXY_CONFIG_KEY, json.dumps(data), encrypted=encrypted)

    async def get_proxy_config(self) -> ProxyConfig:
        """Get proxy configuration."""
        # Try encrypted first, then unencrypted
        value = await self.get(PROXY_CONFIG_KEY, encrypted=True)
        if value is None:
            value = await self.get(PROXY_CONFIG_KEY, encrypted=False)
        if value is None:
            return ProxyConfig()
        try:
            data = json.loads(value)
            return ProxyConfig(
                type=ProxyType(data.get("type", "none")),
                host=data.get("host", ""),
                port=data.get("port", 0),
                username=data.get("username"),
                password=data.get("password"),
            )
        except (json.JSONDecodeError, ValueError):
            return ProxyConfig()

    async def delete_proxy_config(self) -> bool:
        """Delete proxy configuration."""
        return await self.delete(PROXY_CONFIG_KEY)

    # Language-specific methods
    async def save_language_config(self, config: LanguageConfig) -> None:
        """Save language configuration."""
        data = {"primary": config.primary, "fallback": config.fallback}
        await self.set(LANGUAGE_CONFIG_KEY, json.dumps(data))

    async def get_language_config(self) -> LanguageConfig:
        """Get language configuration."""
        value = await self.get(LANGUAGE_CONFIG_KEY)
        if value is None:
            return LanguageConfig()
        try:
            data = json.loads(value)
            return LanguageConfig(
                primary=data.get("primary", "zh-CN"),
                fallback=data.get("fallback", ["en-US"]),
            )
        except (json.JSONDecodeError, ValueError):
            return LanguageConfig()

    # API Token 相关方法
    async def save_api_token(self, token: str) -> None:
        """Save TMDB API token (encrypted)."""
        await self.set(API_TOKEN_KEY, token, encrypted=True)

    async def get_api_token(self) -> str | None:
        """Get TMDB API token (decrypted)."""
        return await self.get(API_TOKEN_KEY, encrypted=True)

    async def delete_api_token(self) -> bool:
        """Delete the stored TMDB API token and related data."""
        deleted = await self.delete(API_TOKEN_KEY)
        await self.delete(API_TOKEN_VERIFIED_KEY)
        await self.delete(API_TOKEN_VERIFIED_AT_KEY)
        # R18 结论跟着 Token 走：换 Token 后旧结论可能不成立
        await self.delete(ADULT_ENABLED_KEY)
        await self.delete(ADULT_MESSAGE_KEY)
        await self.delete(ADULT_CHECKED_AT_KEY)
        return deleted

    async def has_api_token(self) -> bool:
        """Check if API token is configured."""
        return await self.exists(API_TOKEN_KEY)

    async def set_api_token_verified(self, is_valid: bool) -> None:
        """Set API token verification status."""
        await self.set(API_TOKEN_VERIFIED_KEY, "1" if is_valid else "0")
        await self.set(API_TOKEN_VERIFIED_AT_KEY, datetime.now().isoformat())

    async def get_api_token_verification(self) -> tuple[bool | None, datetime | None]:
        """Get API token verification status and timestamp."""
        is_valid_str = await self.get(API_TOKEN_VERIFIED_KEY)
        verified_at_str = await self.get(API_TOKEN_VERIFIED_AT_KEY)

        is_valid = None
        if is_valid_str is not None:
            is_valid = is_valid_str == "1"

        verified_at = None
        if verified_at_str:
            try:
                verified_at = datetime.fromisoformat(verified_at_str)
            except ValueError:
                pass

        return is_valid, verified_at

    async def save_adult_status(self, enabled: bool | None, message: str) -> None:
        """保存 R18 探测结论（None 表示无法判定，同样落库以免每次都重新探测）。"""
        stored = "unknown" if enabled is None else ("1" if enabled else "0")
        await self.set(ADULT_ENABLED_KEY, stored)
        await self.set(ADULT_MESSAGE_KEY, message)
        await self.set(ADULT_CHECKED_AT_KEY, datetime.now().isoformat())

    async def get_adult_status(self) -> tuple[bool | None, str | None, datetime | None]:
        """读 R18 探测结论：无记录时 enabled 为 None、时间也为 None（区分「未检测」与「检测后无法判定」）。"""
        enabled_str = await self.get(ADULT_ENABLED_KEY)
        message = await self.get(ADULT_MESSAGE_KEY)
        checked_at_str = await self.get(ADULT_CHECKED_AT_KEY)

        enabled: bool | None = None
        if enabled_str in ("0", "1"):
            enabled = enabled_str == "1"

        checked_at = None
        if checked_at_str:
            try:
                checked_at = datetime.fromisoformat(checked_at_str)
            except ValueError:
                pass

        return enabled, message, checked_at

    async def get_api_token_status(self) -> ApiTokenStatus:
        """Get current API token configuration status."""
        is_configured = await self.has_api_token()

        if not is_configured:
            return ApiTokenStatus(is_configured=False)

        is_valid, verified_at = await self.get_api_token_verification()
        adult_enabled, adult_message, adult_checked_at = await self.get_adult_status()

        return ApiTokenStatus(
            is_configured=True,
            is_valid=is_valid,
            last_verified=verified_at,
            adult_enabled=adult_enabled,
            adult_message=adult_message,
            adult_checked_at=adult_checked_at,
        )

    # Organize config methods
    async def save_organize_config(self, config: OrganizeConfig) -> None:
        """Save organize configuration."""
        data = {
            "organize_dir": config.organize_dir,
            "metadata_dir": config.metadata_dir,
            "organize_mode": config.organize_mode.value,
            "min_file_size_mb": config.min_file_size_mb,
            "file_type_whitelist": config.file_type_whitelist,
            "filename_blacklist": config.filename_blacklist,
            "junk_pattern_filter": config.junk_pattern_filter,
            "auto_clean_source": config.auto_clean_source,
        }
        await self.set(ORGANIZE_CONFIG_KEY, json.dumps(data))

    async def get_organize_config(self) -> OrganizeConfig:
        """Get organize configuration."""
        value = await self.get(ORGANIZE_CONFIG_KEY)
        if value is None:
            return OrganizeConfig()
        try:
            data = json.loads(value)
            return OrganizeConfig(
                organize_dir=data.get("organize_dir", ""),
                metadata_dir=data.get("metadata_dir", ""),
                organize_mode=OrganizeMode(data.get("organize_mode", "copy")),
                min_file_size_mb=data.get("min_file_size_mb", 100),
                file_type_whitelist=data.get(
                    "file_type_whitelist", ["mkv", "mp4", "avi", "wmv", "ts", "rmvb"]
                ),
                filename_blacklist=data.get("filename_blacklist", ["sample", "trailer"]),
                junk_pattern_filter=data.get("junk_pattern_filter", []),
                auto_clean_source=data.get("auto_clean_source", False),
            )
        except (json.JSONDecodeError, ValueError):
            return OrganizeConfig()

    # Download config methods
    async def save_download_config(self, config: DownloadConfig) -> None:
        """Save download configuration."""
        data = config.model_dump()
        await self.set(DOWNLOAD_CONFIG_KEY, json.dumps(data))

    async def get_download_config(self) -> DownloadConfig:
        """Get download configuration."""
        value = await self.get(DOWNLOAD_CONFIG_KEY)
        if value is None:
            return DownloadConfig()
        try:
            data = json.loads(value)
            return DownloadConfig(**data)
        except (json.JSONDecodeError, ValueError):
            return DownloadConfig()

    # Naming config methods
    async def save_naming_config(self, config: NamingTemplate) -> None:
        """Save naming template configuration."""
        data = config.model_dump()
        await self.set(NAMING_CONFIG_KEY, json.dumps(data))

    async def get_naming_config(self) -> NamingTemplate:
        """Get naming template configuration."""
        value = await self.get(NAMING_CONFIG_KEY)
        if value is None:
            return NamingTemplate()
        try:
            data = json.loads(value)
            return NamingTemplate(**data)
        except (json.JSONDecodeError, ValueError):
            return NamingTemplate()

    # Watcher config methods
    async def save_watcher_config(self, config: WatcherConfig) -> None:
        """Save watcher configuration."""
        data = {
            "enabled": config.enabled,
            "mode": config.mode.value,
            "performance_mode": config.performance_mode,
            "watch_dirs": config.watch_dirs,
        }
        await self.set(WATCHER_CONFIG_KEY, json.dumps(data))

    async def get_watcher_config(self) -> WatcherConfig:
        """Get watcher configuration."""
        value = await self.get(WATCHER_CONFIG_KEY)
        if value is None:
            return WatcherConfig()
        try:
            data = json.loads(value)
            return WatcherConfig(
                enabled=data.get("enabled", False),
                mode=WatcherMode(data.get("mode", "realtime")),
                performance_mode=data.get("performance_mode", False),
                watch_dirs=data.get("watch_dirs", []),
            )
        except (json.JSONDecodeError, ValueError):
            return WatcherConfig()

    # NFO config methods
    async def save_nfo_config(self, config: NfoConfig) -> None:
        """Save NFO configuration."""
        data = config.model_dump()
        await self.set(NFO_CONFIG_KEY, json.dumps(data))

    async def get_nfo_config(self) -> NfoConfig:
        """Get NFO configuration."""
        value = await self.get(NFO_CONFIG_KEY)
        if value is None:
            return NfoConfig()
        try:
            data = json.loads(value)
            return NfoConfig(**data)
        except (json.JSONDecodeError, ValueError):
            return NfoConfig()

    # System config methods
    async def save_system_config(self, config: SystemConfig) -> None:
        """Save system configuration."""
        data = config.model_dump()
        await self.set(SYSTEM_CONFIG_KEY, json.dumps(data))

    async def get_system_config(self) -> SystemConfig:
        """Get system configuration with migration support."""
        value = await self.get(SYSTEM_CONFIG_KEY)
        if value is None:
            # 新安装：尝试从 DownloadConfig 迁移 retry_count/concurrent_downloads
            download_value = await self.get(DOWNLOAD_CONFIG_KEY)
            if download_value:
                try:
                    download_data = json.loads(download_value)
                    defaults = {}
                    if "retry_count" in download_data:
                        defaults["retry_count"] = download_data["retry_count"]
                    if "concurrent_downloads" in download_data:
                        defaults["concurrent_downloads"] = download_data["concurrent_downloads"]
                    if defaults:
                        return SystemConfig(**defaults)
                except (json.JSONDecodeError, ValueError):
                    pass
            return SystemConfig()
        try:
            data = json.loads(value)
            # 迁移: scrape_timeout → task_timeout
            if "scrape_timeout" in data and "task_timeout" not in data:
                data["task_timeout"] = data.pop("scrape_timeout")
            config = SystemConfig(**data)
        except (json.JSONDecodeError, ValueError):
            return SystemConfig()

        return await self._migrate_legacy_task_timeout(config)

    async def _migrate_legacy_task_timeout(self, config: SystemConfig) -> SystemConfig:
        """把「旧版出厂默认 30s」一次性提到 300s。

        30s 是上一版的默认值，但用户也可能主动选过 30，因此不能每次读都改：
        迁移后落标记，之后用户再改回 30 会被尊重。
        """
        if await self.exists(TASK_TIMEOUT_MIGRATED_KEY):
            return config

        migrated = config
        if config.task_timeout == 30:
            migrated = config.model_copy(update={"task_timeout": 300})
            await self.save_system_config(migrated)
            logger.info("系统配置迁移: task_timeout 30 → 300（旧版默认值）")

        await self.set(TASK_TIMEOUT_MIGRATED_KEY, "1")
        return migrated

    # Emby config methods
    async def save_emby_config(self, config: EmbyConfig) -> None:
        """Save Emby configuration (API Key encrypted)."""
        data = config.model_dump()
        await self.set(EMBY_CONFIG_KEY, json.dumps(data), encrypted=True)

    async def get_emby_config(self) -> EmbyConfig:
        """Get Emby configuration."""
        value = await self.get(EMBY_CONFIG_KEY, encrypted=True)
        if value is None:
            return EmbyConfig()
        try:
            data = json.loads(value)
            return EmbyConfig(**data)
        except (json.JSONDecodeError, ValueError):
            return EmbyConfig()

    # 115 cloud config methods
    async def save_115_config(self, config: Cloud115Config) -> None:
        """Save 115 cloud configuration."""
        await self.set(CLOUD_115_CONFIG_KEY, config.model_dump_json(), encrypted=True)

    async def get_115_config(self) -> Cloud115Config:
        """Get 115 cloud configuration."""
        value = await self.get(CLOUD_115_CONFIG_KEY, encrypted=True)
        if value is None:
            return Cloud115Config()
        try:
            return Cloud115Config.model_validate_json(value)
        except ValueError:
            return Cloud115Config()

    async def delete_115_config(self) -> bool:
        """Delete 115 cloud configuration."""
        return await self.delete(CLOUD_115_CONFIG_KEY)
