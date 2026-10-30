"""Unit tests for ConfigService."""

import tempfile
from pathlib import Path

import aiosqlite
import pytest
from pydantic import ValidationError

from server.domain.system.config_service import ConfigService
from server.models.cloud_115 import Cloud115Config
from server.models.storage import StorageLocator, StorageProvider
from server.models.system import SystemConfig
from server.models.template import NamingTemplate


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


@pytest.fixture
def config_service(temp_db):
    """Provide a ConfigService instance with temp database."""
    return ConfigService(db_path=temp_db)


class TestConfigService:
    """Tests for ConfigService class."""

    @pytest.mark.asyncio
    async def test_set_and_get(self, config_service):
        """Test basic set and get operations."""
        await config_service.set("test_key", "test_value")
        result = await config_service.get("test_key")
        assert result == "test_value"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, config_service):
        """Test getting a nonexistent key."""
        result = await config_service.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_encrypted(self, config_service):
        """Test encrypted storage."""
        await config_service.set("secret", "my_secret_value", encrypted=True)

        # Get with decryption
        decrypted = await config_service.get("secret", encrypted=True)
        assert decrypted == "my_secret_value"

        # Get without decryption (should return encrypted value)
        encrypted = await config_service.get("secret", encrypted=False)
        assert encrypted != "my_secret_value"

    @pytest.mark.asyncio
    async def test_delete(self, config_service):
        """Test delete operation."""
        await config_service.set("to_delete", "value")
        assert await config_service.exists("to_delete")

        deleted = await config_service.delete("to_delete")
        assert deleted is True
        assert not await config_service.exists("to_delete")

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, config_service):
        """Test deleting a nonexistent key."""
        deleted = await config_service.delete("nonexistent")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_exists(self, config_service):
        """Test exists check."""
        assert not await config_service.exists("new_key")
        await config_service.set("new_key", "value")
        assert await config_service.exists("new_key")

    @pytest.mark.asyncio
    async def test_update_existing(self, config_service):
        """Test updating an existing key."""
        await config_service.set("key", "value1")
        await config_service.set("key", "value2")
        result = await config_service.get("key")
        assert result == "value2"

    @pytest.mark.asyncio
    async def test_save_cookie(self, config_service):
        """Test cookie save operation."""
        await config_service.save_cookie("session_id=abc123")
        cookie = await config_service.get_cookie()
        assert cookie == "session_id=abc123"

    @pytest.mark.asyncio
    async def test_has_cookie(self, config_service):
        """Test has_cookie check."""
        assert not await config_service.has_cookie()
        await config_service.save_cookie("session_id=abc123")
        assert await config_service.has_cookie()

    @pytest.mark.asyncio
    async def test_delete_cookie(self, config_service):
        """Test cookie deletion."""
        await config_service.save_cookie("session_id=abc123")
        await config_service.set_cookie_verified(True)

        deleted = await config_service.delete_cookie()
        assert deleted is True
        assert not await config_service.has_cookie()

    @pytest.mark.asyncio
    async def test_cookie_verification_status(self, config_service):
        """Test cookie verification status tracking."""
        await config_service.set_cookie_verified(True)
        is_valid, verified_at = await config_service.get_cookie_verification()

        assert is_valid is True
        assert verified_at is not None

    @pytest.mark.asyncio
    async def test_cookie_verification_invalid(self, config_service):
        """Test invalid cookie verification status."""
        await config_service.set_cookie_verified(False)
        is_valid, verified_at = await config_service.get_cookie_verification()

        assert is_valid is False
        assert verified_at is not None

    @pytest.mark.asyncio
    async def test_save_and_get_naming_config(self, config_service):
        """Test saving and reading naming template configuration."""
        config = NamingTemplate(
            series_folder="{title}",
            season_folder="S{season:02d}",
            episode_file="{title}.S{season:02d}E{episode:02d}",
        )

        await config_service.save_naming_config(config)
        result = await config_service.get_naming_config()

        assert result == config


class TestConfigService115:
    """Tests for 115 cloud config support."""

    @pytest.mark.asyncio
    async def test_save_and_get_115_config(self, config_service):
        """Test saving and reading 115 cloud configuration."""
        config = Cloud115Config(
            enabled=True,
            app="alipaymini",
            cookies="UID=1; CID=2; SEID=3",
            is_logged_in=True,
        )

        await config_service.save_115_config(config)
        result = await config_service.get_115_config()

        assert result.enabled is True
        assert result.app == "alipaymini"
        assert result.cookies == "UID=1; CID=2; SEID=3"
        assert result.is_logged_in is True

    @pytest.mark.asyncio
    async def test_save_115_config_stores_encrypted_value(self, config_service):
        """Test 115 cloud configuration is stored encrypted in the database."""
        config = Cloud115Config(
            enabled=True,
            app="alipaymini",
            cookies="UID=1; CID=2; SEID=3",
            is_logged_in=True,
        )

        await config_service.save_115_config(config)

        async with aiosqlite.connect(config_service.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT value, encrypted FROM config WHERE key = ?",
                ("cloud_115_config",),
            )
            row = await cursor.fetchone()

        assert row is not None
        assert row["encrypted"] == 1
        assert row["value"] != config.model_dump_json()
        assert config.cookies not in row["value"]

    @pytest.mark.asyncio
    async def test_delete_115_config_restores_defaults(self, config_service):
        """Test deleting 115 cloud configuration falls back to defaults."""
        config = Cloud115Config(
            enabled=True,
            app="alipaymini",
            cookies="UID=1; CID=2; SEID=3",
            is_logged_in=True,
        )

        await config_service.save_115_config(config)

        deleted = await config_service.delete_115_config()
        result = await config_service.get_115_config()

        assert deleted is True
        assert result == Cloud115Config()

    @pytest.mark.asyncio
    async def test_get_115_config_invalid_data_falls_back_to_defaults(self, config_service):
        """Test invalid 115 cloud configuration falls back to defaults."""
        await config_service.set("cloud_115_config", "not-json", encrypted=True)

        result = await config_service.get_115_config()

        assert result == Cloud115Config()

    def test_storage_locator_defaults(self):
        """Test 115 storage locator structure."""
        locator = StorageLocator(
            provider=StorageProvider.P115,
            path="/115网盘/示例目录",
        )

        assert locator.provider == StorageProvider.P115
        assert locator.path == "/115网盘/示例目录"
        assert locator.file_id is None
        assert locator.parent_id is None
        assert locator.is_dir is True

    @pytest.mark.asyncio
    async def test_get_115_config_bad_ciphertext_falls_back_to_defaults(self, config_service):
        """Test undecryptable 115 cloud configuration falls back to defaults."""
        await config_service._ensure_db()

        async with aiosqlite.connect(config_service.db_path) as db:
            await db.execute(
                """
                INSERT INTO config (key, value, encrypted, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    encrypted = excluded.encrypted,
                    updated_at = excluded.updated_at
                """,
                ("cloud_115_config", "not-a-valid-fernet-token", 1),
            )
            await db.commit()

        result = await config_service.get_115_config()

        assert result == Cloud115Config()


class TestTaskTimeoutMigration:
    """旧版出厂默认 30s 的一次性迁移。"""

    @pytest.mark.asyncio
    async def test_legacy_default_is_bumped_once(self, config_service):
        await config_service.save_system_config(SystemConfig(task_timeout=30))

        migrated = await config_service.get_system_config()

        assert migrated.task_timeout == 300
        # 结论要落库：否则每次读都要再判断一次，用户改回 30 也会被反复覆盖
        assert (await config_service.get_system_config()).task_timeout == 300

    @pytest.mark.asyncio
    async def test_user_chosen_value_is_respected_after_migration(self, config_service):
        await config_service.save_system_config(SystemConfig(task_timeout=30))
        await config_service.get_system_config()

        await config_service.save_system_config(SystemConfig(task_timeout=30))
        assert (await config_service.get_system_config()).task_timeout == 30

    @pytest.mark.asyncio
    async def test_non_default_value_is_untouched(self, config_service):
        await config_service.save_system_config(SystemConfig(task_timeout=120))
        assert (await config_service.get_system_config()).task_timeout == 120

    def test_default_for_new_install(self):
        assert SystemConfig().task_timeout == 300

    def test_upper_bound_is_raised(self):
        assert SystemConfig(task_timeout=1800).task_timeout == 1800
        with pytest.raises(ValidationError):
            SystemConfig(task_timeout=1801)
