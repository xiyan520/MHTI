"""Unit tests for AuthService.

测试 AuthService 的核心功能：
- 密码哈希和验证
- JWT token 创建和验证
- 刷新 token 过期时间计算
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.domain.identity.auth_service import AuthService
from server.models.auth import AuthConfig


@pytest.fixture
def auth_service() -> AuthService:
    """提供一个新的 AuthService 实例。"""
    service = AuthService()
    # 预设配置缓存以避免异步加载
    service._config_cache = AuthConfig(
        jwt_secret="test_secret_key_for_testing_purposes_only",
        access_token_minutes=15,
        max_login_attempts=5,
        lockout_minutes=15,
    )
    return service


class TestPasswordHashing:
    """测试密码哈希功能。"""

    def test_hash_password_returns_hash_and_salt(self, auth_service):
        """测试哈希密码返回哈希值和盐。"""
        password = "test_password_123"
        hash_value, salt = auth_service._hash_password(password)

        assert hash_value is not None
        assert salt is not None
        assert len(hash_value) > 0
        assert len(salt) == 32  # 16 bytes hex = 32 chars

    def test_hash_password_with_provided_salt(self, auth_service):
        """测试使用提供的盐进行哈希。"""
        password = "test_password"
        salt = "fixed_salt_for_test"

        hash1, returned_salt1 = auth_service._hash_password(password, salt)
        hash2, returned_salt2 = auth_service._hash_password(password, salt)

        assert hash1 == hash2
        assert returned_salt1 == salt
        assert returned_salt2 == salt

    def test_hash_password_different_passwords_different_hashes(self, auth_service):
        """测试不同密码产生不同哈希。"""
        salt = "fixed_salt"

        hash1, _ = auth_service._hash_password("password1", salt)
        hash2, _ = auth_service._hash_password("password2", salt)

        assert hash1 != hash2

    def test_verify_password_correct(self, auth_service):
        """测试正确密码验证成功。"""
        password = "correct_password"
        # 当前存储格式：算法$迭代数$盐$哈希
        stored_hash = auth_service._format_password_hash(password)

        assert auth_service._verify_password(password, stored_hash) is True

    def test_verify_password_incorrect(self, auth_service):
        """测试错误密码验证失败。"""
        password = "correct_password"
        stored_hash = auth_service._format_password_hash(password)

        assert auth_service._verify_password("wrong_password", stored_hash) is False

    def test_verify_password_invalid_format(self, auth_service):
        """测试无效格式的存储哈希返回 False。"""
        assert auth_service._verify_password("any", "invalid_hash_without_dollar") is False


class TestJWTToken:
    """测试 JWT Token 功能。"""

    def test_create_access_token(self, auth_service):
        """测试创建访问 token。"""
        username = "test_user"
        session_id = "session_123"

        token, expires_in = auth_service.create_access_token(username, session_id)

        assert token is not None
        assert len(token) > 0
        assert expires_in == 15 * 60  # 15 minutes in seconds

    def test_verify_token_valid(self, auth_service):
        """测试验证有效 token。"""
        username = "test_user"
        session_id = "session_456"

        token, _ = auth_service.create_access_token(username, session_id)
        verified_username, verified_session_id = auth_service.verify_token(token)

        assert verified_username == username
        assert verified_session_id == session_id

    def test_verify_token_invalid(self, auth_service):
        """测试验证无效 token 返回 None。"""
        invalid_token = "invalid.jwt.token"

        username, session_id = auth_service.verify_token(invalid_token)

        assert username is None
        assert session_id is None

    def test_verify_token_tampered(self, auth_service):
        """测试被篡改的 token 验证失败。"""
        token, _ = auth_service.create_access_token("user", "session")
        # 篡改 token
        tampered_token = token[:-5] + "xxxxx"

        username, session_id = auth_service.verify_token(tampered_token)

        assert username is None
        assert session_id is None


class TestRefreshExpire:
    """测试刷新 token 过期时间计算。"""

    def test_get_refresh_expire_one_hour(self, auth_service):
        """测试 1 小时过期时间。"""
        seconds = auth_service.get_refresh_expire_seconds("1h")
        assert seconds == 1 * 3600

    def test_get_refresh_expire_one_day(self, auth_service):
        """测试 1 天过期时间。"""
        seconds = auth_service.get_refresh_expire_seconds("1d")
        assert seconds == 24 * 3600

    def test_get_refresh_expire_one_week(self, auth_service):
        """测试 7 天过期时间。"""
        seconds = auth_service.get_refresh_expire_seconds("7d")
        assert seconds == 7 * 24 * 3600

    def test_get_refresh_expire_one_month(self, auth_service):
        """测试 30 天过期时间。"""
        seconds = auth_service.get_refresh_expire_seconds("30d")
        assert seconds == 30 * 24 * 3600

    def test_get_refresh_expire_never(self, auth_service):
        """测试永不过期（365天）。"""
        seconds = auth_service.get_refresh_expire_seconds("never")
        assert seconds == 365 * 24 * 3600


class TestConfigCache:
    """测试配置缓存功能。"""

    def test_refresh_config_cache(self, auth_service):
        """测试刷新配置缓存。"""
        assert auth_service._config_cache is not None

        auth_service.refresh_config_cache()

        assert auth_service._config_cache is None

    def test_get_config_sync_uses_cache(self, auth_service):
        """测试同步获取配置使用缓存。"""
        config = auth_service._get_config_sync()

        assert config.jwt_secret == "test_secret_key_for_testing_purposes_only"
        assert config.access_token_minutes == 15


class TestAuthServiceAsync:
    """测试需要数据库的异步功能（使用 mock）。"""

    @pytest.mark.asyncio
    async def test_is_initialized_no_admin(self, auth_service):
        """测试没有管理员时返回 False。"""
        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(0,))
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_manager = AsyncMock()
        mock_manager.get_connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_db),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("server.domain.identity.auth_service.get_db_manager", return_value=mock_manager):
            result = await auth_service.is_initialized()

        assert result is False

    @pytest.mark.asyncio
    async def test_is_initialized_has_admin(self, auth_service):
        """测试有管理员时返回 True。"""
        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(1,))
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_manager = AsyncMock()
        mock_manager.get_connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_db),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("server.domain.identity.auth_service.get_db_manager", return_value=mock_manager):
            result = await auth_service.is_initialized()

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_credentials_success(self, auth_service):
        """测试正确凭据验证成功。"""
        password = "test_password"
        stored_hash = auth_service._format_password_hash(password)

        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(stored_hash,))
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_manager = AsyncMock()
        mock_manager.get_connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_db),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("server.domain.identity.auth_service.get_db_manager", return_value=mock_manager):
            result = await auth_service.verify_credentials("admin", password)

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_credentials_wrong_password(self, auth_service):
        """测试错误密码验证失败。"""
        password = "test_password"
        stored_hash = auth_service._format_password_hash(password)

        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=(stored_hash,))
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_manager = AsyncMock()
        mock_manager.get_connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_db),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("server.domain.identity.auth_service.get_db_manager", return_value=mock_manager):
            result = await auth_service.verify_credentials("admin", "wrong_password")

        assert result is False

    @pytest.mark.asyncio
    async def test_verify_credentials_user_not_found(self, auth_service):
        """测试用户不存在时验证失败。"""
        mock_db = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_cursor)

        mock_manager = AsyncMock()
        mock_manager.get_connection = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_db),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("server.domain.identity.auth_service.get_db_manager", return_value=mock_manager):
            result = await auth_service.verify_credentials("nonexistent", "password")

        assert result is False



class TestPasswordHashUpgrade:
    """口令哈希格式升级：新格式自描述，旧格式仍可验证并在登录时升级。"""

    def test_new_hash_is_self_describing(self, auth_service):
        stored = auth_service._format_password_hash("pw")
        parts = stored.split("$")

        assert parts[0] == "pbkdf2_sha256"
        assert int(parts[1]) == auth_service._PBKDF2_ITERATIONS
        assert len(parts) == 4

    def test_legacy_hash_still_verifies(self, auth_service):
        # 旧格式 salt$hash，当年写死 10 万轮
        hash_value, salt = auth_service._hash_password("pw", iterations=100_000)
        legacy = f"{salt}${hash_value}"

        assert auth_service._verify_password("pw", legacy) is True

    def test_legacy_hash_is_flagged_for_rehash(self, auth_service):
        hash_value, salt = auth_service._hash_password("pw", iterations=100_000)

        assert auth_service._needs_rehash(f"{salt}${hash_value}") is True
        assert auth_service._needs_rehash(auth_service._format_password_hash("pw")) is False

    def test_wrong_password_is_rejected(self, auth_service):
        stored = auth_service._format_password_hash("pw")

        assert auth_service._verify_password("bad", stored) is False

    def test_malformed_hash_is_rejected(self, auth_service):
        assert auth_service._verify_password("pw", "not-a-hash") is False
        assert auth_service._verify_password("pw", "pbkdf2_sha256$abc$salt$hash") is False

    @pytest.mark.asyncio
    async def test_login_upgrades_legacy_hash_in_place(self, auth_service):
        hash_value, salt = auth_service._hash_password("pw", iterations=100_000)
        legacy = f"{salt}${hash_value}"

        auth_service._repo = MagicMock()
        auth_service._repo.get_password_hash = AsyncMock(return_value=legacy)
        auth_service._repo.update_password = AsyncMock()

        assert await auth_service.verify_credentials("admin", "pw") is True

        auth_service._repo.update_password.assert_awaited_once()
        _username, new_hash = auth_service._repo.update_password.await_args.args
        assert _username == "admin"
        assert new_hash.startswith("pbkdf2_sha256$600000$")
        assert auth_service._verify_password("pw", new_hash) is True
