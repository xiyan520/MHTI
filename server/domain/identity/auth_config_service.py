"""Authentication configuration service - database-backed."""

import asyncio
import logging
import secrets
from datetime import datetime, timezone

from server.infrastructure.repositories.auth_config_repository import AuthConfigRepository
from server.infrastructure.security import decrypt_value, encrypt_value
from server.models.auth import AuthConfig

logger = logging.getLogger(__name__)

# Auth config keys
AUTH_CONFIG_KEYS = {
    "jwt_secret": True,           # encrypted
    "max_login_attempts": False,
    "lockout_minutes": False,
    "access_token_minutes": False,
    "max_sessions": False,
}

# Default values
DEFAULT_AUTH_CONFIG = {
    "max_login_attempts": "5",
    "lockout_minutes": "15",
    "access_token_minutes": "15",
    "max_sessions": "10",
}


class AuthConfigService:
    """Service for managing authentication configuration in database."""

    _instance: "AuthConfigService | None" = None
    _lock: asyncio.Lock = asyncio.Lock()
    _jwt_secret_cache: str | None = None

    def __init__(self) -> None:
        self._repo = AuthConfigRepository()

    @classmethod
    async def get_instance(cls) -> "AuthConfigService":
        """Get singleton instance."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = AuthConfigService()
        return cls._instance

    @classmethod
    def get_sync(cls) -> "AuthConfigService":
        """Get instance synchronously (use only if already initialized)."""
        if cls._instance is None:
            cls._instance = AuthConfigService()
        return cls._instance

    async def get_jwt_secret(self) -> str:
        """
        Get or create JWT secret from database.

        Returns:
            JWT secret string
        """
        # Return cached value if available
        if self._jwt_secret_cache:
            return self._jwt_secret_cache

        try:
            row = await self._repo.get_value("jwt_secret")

            if row:
                value, encrypted = row
                if encrypted:
                    secret = decrypt_value(value)
                    # If decryption failed (key changed), regenerate secret
                    if secret is None:
                        logger.warning("JWT Secret: decryption failed, regenerating...")
                        row = None  # Fall through to generate new secret
                else:
                    secret = value

                if row:  # Only use if decryption succeeded
                    self._jwt_secret_cache = secret
                    logger.info("JWT Secret: loaded from database")
                    return secret

            # Generate new secret
            new_secret = secrets.token_urlsafe(32)
            encrypted_secret = encrypt_value(new_secret)
            now = datetime.now(timezone.utc).isoformat()

            # Use REPLACE to handle both insert and update
            await self._repo.replace_value("jwt_secret", encrypted_secret, 1, now, now)

            self._jwt_secret_cache = new_secret
            logger.warning("JWT Secret: generated new secret and saved to database")
            return new_secret

        except Exception as e:
            logger.error(f"Failed to get JWT secret from database: {e}")
            # Fallback: generate temporary secret (not persisted)
            if not self._jwt_secret_cache:
                self._jwt_secret_cache = secrets.token_urlsafe(32)
                logger.warning("JWT Secret: using temporary secret (not persisted)")
            return self._jwt_secret_cache

    async def get_config_value(self, key: str) -> str | None:
        """Get a configuration value from database."""
        try:
            row = await self._repo.get_value(key)
            if not row:
                return DEFAULT_AUTH_CONFIG.get(key)

            value, encrypted = row

            if encrypted:
                return decrypt_value(value)
            return value

        except Exception as e:
            logger.error(f"Failed to get config value '{key}': {e}")
            return DEFAULT_AUTH_CONFIG.get(key)

    async def set_config_value(self, key: str, value: str, encrypted: bool = False) -> bool:
        """Set a configuration value in database."""
        try:
            store_value = encrypt_value(value) if encrypted else value
            now = datetime.now(timezone.utc).isoformat()

            await self._repo.upsert_value(key, store_value, 1 if encrypted else 0, now, now)

            # Clear cache if updating jwt_secret
            if key == "jwt_secret":
                self._jwt_secret_cache = value

            return True

        except Exception as e:
            logger.error(f"Failed to set config value '{key}': {e}")
            return False

    async def get_auth_config(self) -> AuthConfig:
        """Get full authentication configuration from database."""
        jwt_secret = await self.get_jwt_secret()

        max_login_attempts = await self.get_config_value("max_login_attempts")
        lockout_minutes = await self.get_config_value("lockout_minutes")
        access_token_minutes = await self.get_config_value("access_token_minutes")
        max_sessions = await self.get_config_value("max_sessions")

        return AuthConfig(
            jwt_secret=jwt_secret,
            max_login_attempts=int(max_login_attempts or 5),
            lockout_minutes=int(lockout_minutes or 15),
            access_token_minutes=int(access_token_minutes or 15),
            max_sessions=int(max_sessions or 10),
        )

    async def update_auth_config(
        self,
        max_login_attempts: int | None = None,
        lockout_minutes: int | None = None,
        access_token_minutes: int | None = None,
        max_sessions: int | None = None,
    ) -> bool:
        """Update authentication configuration in database."""
        try:
            if max_login_attempts is not None:
                await self.set_config_value("max_login_attempts", str(max_login_attempts))
            if lockout_minutes is not None:
                await self.set_config_value("lockout_minutes", str(lockout_minutes))
            if access_token_minutes is not None:
                await self.set_config_value("access_token_minutes", str(access_token_minutes))
            if max_sessions is not None:
                await self.set_config_value("max_sessions", str(max_sessions))
            return True
        except Exception as e:
            logger.error(f"Failed to update auth config: {e}")
            return False

    def clear_cache(self) -> None:
        """Clear cached values."""
        self._jwt_secret_cache = None


# Helper functions for synchronous access
def get_auth_config_service() -> AuthConfigService:
    """Get the auth config service instance (sync)."""
    return AuthConfigService.get_sync()


async def get_auth_config_service_async() -> AuthConfigService:
    """Get the auth config service instance (async)."""
    return await AuthConfigService.get_instance()
