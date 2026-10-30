"""Database connection pool management."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiosqlite

from server.infrastructure.config import DATA_DIR

logger = logging.getLogger(__name__)

# 数据库路径跟随 DATA_DIR（默认项目根 data/），不再单独算一遍项目根
DATABASE_PATH = DATA_DIR / "scraper.db"


class DatabaseManager:
    """
    Database connection manager with connection pooling.

    Implements a simple connection pool pattern for SQLite with proper
    initialization and cleanup lifecycle management.
    """

    _instance: "DatabaseManager | None" = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._initialized = False
        self._pool: list[aiosqlite.Connection] = []
        self._pool_size = 5
        self._in_use: set[aiosqlite.Connection] = set()
        self._pool_lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> "DatabaseManager":
        """Get singleton instance of DatabaseManager."""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = DatabaseManager()
                    await cls._instance._initialize()
        return cls._instance

    async def _initialize(self) -> None:
        """Initialize database and create tables."""
        from server.infrastructure.db.schema import create_all_tables

        if self._initialized:
            return

        logger.info(f"Database path: {DATABASE_PATH}")
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await configure_connection(db)
            await create_all_tables(db)
            await db.commit()

        self._initialized = True
        logger.info("Database initialized successfully")

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """
        Get a database connection from the pool.

        Usage:
            async with db_manager.get_connection() as db:
                await db.execute(...)
        """
        conn: aiosqlite.Connection | None = None

        async with self._pool_lock:
            if self._pool:
                conn = self._pool.pop()
                self._in_use.add(conn)

        if conn is None:
            conn = await aiosqlite.connect(DATABASE_PATH)
            await configure_connection(conn)
            conn.row_factory = aiosqlite.Row
            async with self._pool_lock:
                self._in_use.add(conn)

        try:
            yield conn
        finally:
            async with self._pool_lock:
                self._in_use.discard(conn)
                if len(self._pool) < self._pool_size:
                    self._pool.append(conn)
                else:
                    await conn.close()

    async def close_all(self) -> None:
        """Close all connections in the pool."""
        async with self._pool_lock:
            for conn in self._pool:
                await conn.close()
            self._pool.clear()

            for conn in self._in_use:
                await conn.close()
            self._in_use.clear()

        logger.info("All database connections closed")


async def configure_connection(db: aiosqlite.Connection) -> None:
    """Configure database connection with optimal settings."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=30000")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA cache_size=-64000")  # 64MB cache
    await db.execute("PRAGMA temp_store=MEMORY")


# Singleton access functions
async def get_db_manager() -> DatabaseManager:
    """Get the database manager singleton."""
    return await DatabaseManager.get_instance()


@asynccontextmanager
async def db_context() -> AsyncGenerator[aiosqlite.Connection, None]:
    """获取数据库连接的简化上下文管理器。

    Usage:
        async with db_context() as db:
            await db.execute(...)

    比直接使用 get_db_manager().get_connection() 更简洁。
    """
    manager = await get_db_manager()
    async with manager.get_connection() as db:
        yield db


async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    Get database connection (FastAPI dependency compatible).

    Usage in API routes:
        @router.get("/")
        async def handler(db: aiosqlite.Connection = Depends(get_db)):
            ...
    """
    manager = await get_db_manager()
    async with manager.get_connection() as db:
        yield db


async def init_database() -> None:
    """Initialize the database (called at application startup)."""
    await get_db_manager()


async def close_database() -> None:
    """Close all database connections (called at application shutdown)."""
    if DatabaseManager._instance:
        await DatabaseManager._instance.close_all()
        DatabaseManager._instance = None
