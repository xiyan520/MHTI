"""Application configuration - static paths + injected auth config cache."""

import logging
import os
import secrets
from pathlib import Path

from server.models.auth import AuthConfig

logger = logging.getLogger(__name__)

# Project root directory
_PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

def _resolve_data_dir(raw: str | None) -> Path:
    """数据目录：DATA_DIR 环境变量优先，缺省落在项目根的 data/。

    环境变量长期只写在 Dockerfile / compose 里、代码却写死项目根，
    结果换宿主机挂载点时库和密钥仍被写到别处（容器内恰好都是 /app/data 才没暴露）。
    """
    if raw:
        return Path(raw).expanduser().resolve()
    return _PROJECT_ROOT / "data"


# Data directory（SQLite、密钥、日志、115 客户端缓存都挂在这下面）
DATA_DIR = _resolve_data_dir(os.getenv("DATA_DIR"))


class AppConfig:
    """Application configuration - static paths, database-backed auth."""

    _auth_config_cache: AuthConfig | None = None

    @property
    def data_dir(self) -> Path:
        """Data directory path (fixed)."""
        return DATA_DIR

    @property
    def auth(self) -> AuthConfig:
        """
        Get authentication configuration synchronously.

        For initial app startup, returns default config.
        Use get_auth_async() for database-backed config.
        """
        if self._auth_config_cache:
            return self._auth_config_cache

        # Return default config for sync access before DB is ready
        return AuthConfig(
            max_login_attempts=5,
            lockout_minutes=15,
            jwt_secret=self._get_fallback_jwt_secret(),
            access_token_minutes=15,
            max_sessions=10,
        )

    def set_auth_config(self, config: AuthConfig) -> None:
        """注入从数据库加载的认证配置（由组合根在启动时调用）。"""
        self._auth_config_cache = config

    def refresh_auth_cache(self) -> None:
        """Clear auth config cache to force reload from database."""
        self._auth_config_cache = None

    def _get_fallback_jwt_secret(self) -> str:
        """
        Get fallback JWT secret for sync access.

        This is only used during initial startup before DB is available.
        """
        # Generate a temporary secret for initial startup
        return secrets.token_urlsafe(32)


# Singleton instance
_app_config: AppConfig | None = None


def get_app_config() -> AppConfig:
    """Get application configuration (singleton)."""
    global _app_config
    if _app_config is None:
        _app_config = AppConfig()
    return _app_config


def clear_auth_config_cache() -> None:
    """Clear auth config cache."""
    if _app_config:
        _app_config.refresh_auth_cache()
