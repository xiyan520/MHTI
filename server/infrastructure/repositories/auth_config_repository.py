"""auth_config 表仓储。"""

from __future__ import annotations

from server.infrastructure.repositories.base import BaseRepository


class AuthConfigRepository(BaseRepository):
    """认证配置表访问。"""

    async def get_value(self, key: str) -> tuple[str, int] | None:
        """读取配置值，返回 (value, encrypted)；不存在返回 None。"""
        row = await self._fetch_one(
            "SELECT value, encrypted FROM auth_config WHERE key = ?", (key,)
        )
        if row is None:
            return None
        return row[0], row[1]

    async def replace_value(
        self, key: str, value: str, encrypted: int, created_at: str, updated_at: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO auth_config (key, value, encrypted, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, value, encrypted, created_at, updated_at),
            )
            await db.commit()

    async def upsert_value(
        self, key: str, value: str, encrypted: int, created_at: str, updated_at: str
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO auth_config (key, value, encrypted, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    encrypted = excluded.encrypted,
                    updated_at = excluded.updated_at
                """,
                (key, value, encrypted, created_at, updated_at),
            )
            await db.commit()
