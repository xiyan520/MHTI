"""refresh token 轮换：每次刷新换新值，旧值只在宽限期内可用。

轮换之前 refresh token 是「一次签发、7 天可反复续期」的长期凭证，泄露即长期通行证。
这里覆盖轮换后的三个约束：新 token 生效、旧 token 仅在宽限期内可用、库里只存哈希。
"""

import hashlib
from pathlib import Path

import aiosqlite
import pytest

from server.domain.identity.session_service import SessionService
from server.infrastructure.db import create_all_tables


async def _init_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


@pytest.fixture
def session_service(temp_db: Path, monkeypatch: pytest.MonkeyPatch) -> SessionService:
    service = SessionService(db_path=temp_db)

    async def _max_sessions() -> int:
        # 默认实现要读全局认证配置（会打开真实库），测试里固定住
        return 10

    monkeypatch.setattr(service, "_get_max_sessions", _max_sessions)
    return service


@pytest.mark.asyncio
async def test_rotation_issues_new_token_and_expires_old_one(
    temp_db: Path, session_service: SessionService
) -> None:
    await _init_db(temp_db)
    session_id, refresh_token = await session_service.create_session(
        user_id=1, expire_option="7d"
    )

    rotated = await session_service.rotate_refresh_token(session_id, grace_seconds=0)

    assert rotated is not None
    assert rotated != refresh_token

    # 新 token 立即可用
    assert (await session_service.verify_refresh_token(rotated))[0] == session_id
    # 宽限为 0：旧 token 必须立刻失效
    assert await session_service.verify_refresh_token(refresh_token) == (None, None)


@pytest.mark.asyncio
async def test_old_token_still_works_inside_grace_window(
    temp_db: Path, session_service: SessionService
) -> None:
    await _init_db(temp_db)
    session_id, refresh_token = await session_service.create_session(
        user_id=1, expire_option="7d"
    )

    await session_service.rotate_refresh_token(session_id)

    # 宽限期内旧 token 仍可用，避免多标签页并发刷新互相踢下线
    assert (await session_service.verify_refresh_token(refresh_token))[0] == session_id


@pytest.mark.asyncio
async def test_only_hash_is_stored(temp_db: Path, session_service: SessionService) -> None:
    await _init_db(temp_db)
    session_id, _old_token = await session_service.create_session(
        user_id=1, expire_option="7d"
    )

    rotated = await session_service.rotate_refresh_token(session_id)
    assert rotated is not None

    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute(
            "SELECT refresh_token_hash FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()

    assert row is not None
    assert row[0] == hashlib.sha256(rotated.encode()).hexdigest()
    assert row[0] != rotated


@pytest.mark.asyncio
async def test_rotation_for_unknown_session_returns_none(
    temp_db: Path, session_service: SessionService
) -> None:
    await _init_db(temp_db)

    assert await session_service.rotate_refresh_token("no-such-session") is None


@pytest.mark.asyncio
async def test_legacy_sessions_table_gets_rotation_columns(temp_db: Path) -> None:
    """老库的 sessions 表没有轮换列，建表时的迁移要补上。"""
    async with aiosqlite.connect(temp_db) as db:
        await db.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                refresh_token_hash TEXT NOT NULL,
                device_name TEXT,
                device_type TEXT DEFAULT 'desktop',
                ip_address TEXT,
                user_agent TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        await db.commit()

    await _init_db(temp_db)

    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute("PRAGMA table_info(sessions)")
        columns = {row[1] for row in await cursor.fetchall()}

    assert {"prev_refresh_token_hash", "prev_valid_until"} <= columns
