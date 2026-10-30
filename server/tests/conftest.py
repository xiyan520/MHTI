"""Pytest configuration and shared fixtures.

提供所有测试共用的 fixtures：
- 认证 mock (mock_auth_context)
- 数据库临时文件 (temp_db)
- 配置服务 (config_service)
- 测试客户端基础设施
"""

import tempfile
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from server.api.deps import AuthContext, require_auth
from server.main import app

# =============================================================================
# Authentication Fixtures
# =============================================================================


@pytest.fixture
def mock_auth_context() -> AuthContext:
    """
    提供模拟的认证上下文。

    Returns:
        AuthContext with test user credentials.
    """
    return AuthContext(username="test_user", session_id="test_session_123")


@pytest.fixture
def override_auth(mock_auth_context: AuthContext) -> Generator[None, None, None]:
    """
    覆盖 require_auth 依赖，绕过认证检查。

    在测试中使用此 fixture 可跳过认证，直接返回测试用户上下文。

    Usage:
        def test_api_endpoint(client, override_auth):
            response = client.get("/api/protected-endpoint")
            assert response.status_code == 200
    """

    async def mock_require_auth() -> AuthContext:
        return mock_auth_context

    app.dependency_overrides[require_auth] = mock_require_auth
    yield
    # 清理：移除此依赖覆盖
    if require_auth in app.dependency_overrides:
        del app.dependency_overrides[require_auth]


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """
    创建测试用临时数据库文件。

    Returns:
        Path to temporary database file.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        yield db_path


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """
    创建测试用临时目录。

    Returns:
        Path to temporary directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# =============================================================================
# Service Fixtures
# =============================================================================


@pytest.fixture
def file_service():
    """Provide a FileService instance for testing."""
    from server.domain.media.file_service import FileService

    return FileService()


@pytest.fixture
def config_service(temp_db: Path):
    """
    提供使用临时数据库的 ConfigService 实例。

    Args:
        temp_db: Temporary database path from fixture.

    Returns:
        ConfigService instance with isolated database.
    """
    from server.domain.system.config_service import ConfigService

    return ConfigService(db_path=temp_db)


# =============================================================================
# Test Client Fixtures
# =============================================================================


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """
    提供基础测试客户端（无认证覆盖）。

    注意：此客户端不会绕过认证，适用于测试认证流程本身。
    大多数 API 测试应使用 auth_client fixture。
    """
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(override_auth) -> Generator[TestClient, None, None]:
    """
    提供已认证的测试客户端。

    此客户端自动绕过 require_auth 依赖，适用于大多数 API 测试。

    Usage:
        def test_protected_endpoint(auth_client):
            response = auth_client.get("/api/protected")
            assert response.status_code == 200
    """
    yield TestClient(app)
    app.dependency_overrides.clear()
