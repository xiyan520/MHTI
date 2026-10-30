"""history_records 表仓储。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository
from server.models.history import HistoryConflictType, HistoryRecordCreate, TaskStatus

_HISTORY_RECORDS_DDL = """
    CREATE TABLE IF NOT EXISTS history_records (
        id TEXT PRIMARY KEY,
        display_id INTEGER,
        task_name TEXT NOT NULL,
        folder_path TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        status TEXT NOT NULL,
        source TEXT DEFAULT 'manual',
        total_files INTEGER NOT NULL,
        success_count INTEGER NOT NULL,
        failed_count INTEGER NOT NULL,
        duration_seconds REAL NOT NULL,
        error_message TEXT,
        manual_job_id INTEGER,
        scrape_job_id TEXT,
        file_fingerprint TEXT,
        title TEXT,
        original_title TEXT,
        plot TEXT,
        tags TEXT,
        cover_url TEXT,
        poster_url TEXT,
        thumb_url TEXT,
        release_date TEXT,
        rating REAL,
        votes INTEGER,
        translator TEXT,
        scrape_logs TEXT,
        conflict_type TEXT,
        conflict_data TEXT,
        season_number INTEGER,
        episode_number INTEGER,
        tmdb_id INTEGER,  -- 已确定的 TMDB ID（重试时直接沿用）
        episode_title TEXT,
        episode_overview TEXT,
        episode_still_url TEXT,
        episode_air_date TEXT,
        started_at TEXT,  -- 本次执行开始时间（running 期间前端据此实时计时）
        timeout_step TEXT,  -- 超时停在哪一步
        timeout_seconds INTEGER  -- 超时阈值（秒）
    )
"""

# 旧库缺失列迁移清单
_MIGRATION_COLUMNS = [
    ("display_id", "INTEGER"),
    ("manual_job_id", "INTEGER"),
    ("title", "TEXT"),
    ("original_title", "TEXT"),
    ("plot", "TEXT"),
    ("tags", "TEXT"),
    ("cover_url", "TEXT"),
    ("poster_url", "TEXT"),
    ("thumb_url", "TEXT"),
    ("release_date", "TEXT"),
    ("rating", "REAL"),
    ("votes", "INTEGER"),
    ("translator", "TEXT"),
    ("scrape_logs", "TEXT"),
    ("conflict_type", "TEXT"),
    ("conflict_data", "TEXT"),
    # 季/集信息
    ("season_number", "INTEGER"),
    ("episode_number", "INTEGER"),
    ("episode_title", "TEXT"),
    ("episode_overview", "TEXT"),
    ("episode_still_url", "TEXT"),
    ("episode_air_date", "TEXT"),
    ("source", "TEXT DEFAULT 'manual'"),
    ("scrape_job_id", "TEXT"),
    ("file_fingerprint", "TEXT"),  # 文件指纹，用于去重
    ("started_at", "TEXT"),  # 本次执行开始时间，running 期间供前端实时计时
    ("timeout_step", "TEXT"),  # 超时停在哪一步
    ("timeout_seconds", "INTEGER"),  # 超时阈值（秒）
    ("tmdb_id", "INTEGER"),  # 已确定的 TMDB ID（重试时直接沿用）
]


class HistoryRepository(BaseRepository):
    """刮削历史记录表访问。"""

    async def _ensure_custom_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_HISTORY_RECORDS_DDL)
        await db.commit()

    async def ensure_schema(self) -> None:
        """兼容旧数据库：补齐缺失列（幂等）。"""
        async with self._connect() as db:
            for col_name, col_type in _MIGRATION_COLUMNS:
                try:
                    await db.execute(
                        f"ALTER TABLE history_records ADD COLUMN {col_name} {col_type}"
                    )
                except Exception:
                    pass  # 列已存在
            await db.commit()

    async def insert_record(
        self,
        record_id: str,
        data: HistoryRecordCreate,
        *,
        executed_at: str,
        conflict_data_json: str | None,
        scrape_logs_json: str | None,
        started_at: datetime | None = None,
    ) -> int:
        """插入历史记录，返回分配的 display_id。"""
        async with self._connect() as db:
            # 获取下一个 display_id
            cursor = await db.execute(
                "SELECT COALESCE(MAX(display_id), 0) + 1 FROM history_records"
            )
            row = await cursor.fetchone()
            display_id = row[0] if row else 1

            await db.execute(
                """
                INSERT INTO history_records
                (id, display_id, task_name, folder_path, executed_at, status, source, total_files,
                 success_count, failed_count, duration_seconds, error_message,
                 manual_job_id, scrape_job_id, file_fingerprint, conflict_type, conflict_data, scrape_logs,
                 started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    display_id,
                    data.task_name,
                    data.folder_path,
                    executed_at,
                    data.status.value,
                    data.source.value,
                    data.total_files,
                    data.success_count,
                    data.failed_count,
                    data.duration_seconds,
                    data.error_message,
                    data.manual_job_id,
                    data.scrape_job_id,
                    data.file_fingerprint,
                    data.conflict_type.value if data.conflict_type else None,
                    conflict_data_json,
                    scrape_logs_json,
                    started_at.isoformat() if started_at else None,
                ),
            )
            await db.commit()

        return display_id

    async def list_records(
        self,
        limit: int = 100,
        offset: int = 0,
        manual_job_id: int | None = None,
        search: str | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[list[aiosqlite.Row], int]:
        """分页列出记录。

        优化策略：
        - 第一页使用快速模式，避免 COUNT 查询
        - 后续页面使用 COUNT 查询确保分页正确
        """
        conditions = []
        params = []
        if manual_job_id is not None:
            conditions.append("manual_job_id = ?")
            params.append(manual_job_id)
        if search:
            # 搜索 title, folder_path, task_name
            conditions.append("(title LIKE ? OR folder_path LIKE ? OR task_name LIKE ?)")
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._connect() as db:
            # 优化：第一页且无筛选条件时使用快速模式
            is_first_page = offset == 0
            has_filters = bool(conditions)

            if is_first_page and not has_filters:
                # 快速模式：第一页且无筛选时，假设 total 足够大
                # 多查一条判断是否有更多数据
                cursor = await db.execute(
                    """
                    SELECT * FROM history_records
                    ORDER BY executed_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [limit + 1, offset],
                )
                rows = await cursor.fetchall()

                records = list(rows[:limit])

                # 如果数据不足 limit+1 条，说明已经到最后一页
                if len(rows) <= limit:
                    total = len(records)
                else:
                    # 需要获取实际总数（仅当有更多数据时）
                    cursor = await db.execute(
                        "SELECT COUNT(*) as count FROM history_records"
                    )
                    row = await cursor.fetchone()
                    total = row["count"] if row else len(records)
            else:
                # 标准模式：需要 COUNT 查询
                cursor = await db.execute(
                    f"SELECT COUNT(*) as count FROM history_records {where_clause}",
                    params,
                )
                row = await cursor.fetchone()
                total = row["count"] if row else 0

                # Get records
                cursor = await db.execute(
                    f"""
                    SELECT * FROM history_records {where_clause}
                    ORDER BY executed_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    params + [limit, offset],
                )
                rows = await cursor.fetchall()
                records = list(rows)

        return records, total

    async def get_record_raw(self, record_id: str) -> aiosqlite.Row | None:
        return await self._fetch_one(
            "SELECT * FROM history_records WHERE id = ?", (record_id,)
        )

    async def get_fingerprints(self, fingerprints: list[str]) -> set[str]:
        if not fingerprints:
            return set()

        placeholders = ",".join("?" * len(fingerprints))
        rows = await self._fetch_all(
            f"SELECT DISTINCT file_fingerprint FROM history_records WHERE file_fingerprint IN ({placeholders})",
            tuple(fingerprints),
        )
        return {row[0] for row in rows if row[0]}

    async def delete_record(self, record_id: str) -> bool:
        """删除历史记录并级联删除关联的 scrape_job。"""
        async with self._connect() as db:
            # 先获取关联的 scrape_job_id
            cursor = await db.execute(
                "SELECT scrape_job_id FROM history_records WHERE id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            scrape_job_id = row[0] if row else None

            # 删除历史记录
            cursor = await db.execute(
                "DELETE FROM history_records WHERE id = ?",
                (record_id,),
            )
            deleted = cursor.rowcount > 0

            # 同时删除关联的 scrape_job
            if scrape_job_id:
                await db.execute(
                    "DELETE FROM scrape_jobs WHERE id = ?",
                    (scrape_job_id,),
                )

            await db.commit()
            return deleted

    async def list_rows_raw(self, before_days: int | None = None) -> list[dict]:
        """列出即将被清空的整行快照（撤销删除用）。

        与 ``clear_records`` 的筛选条件保持一致，保证「快照 = 会被删掉的那些行」。
        """
        async with self._connect() as db:
            if before_days is not None:
                cutoff = (datetime.now() - timedelta(days=before_days)).isoformat()
                cursor = await db.execute(
                    "SELECT * FROM history_records WHERE executed_at < ?",
                    (cutoff,),
                )
            else:
                cursor = await db.execute("SELECT * FROM history_records")
            return [dict(row) for row in await cursor.fetchall()]

    async def restore_row(self, row: dict) -> None:
        """按快照原样插回一行（撤销删除用，display_id / executed_at 均保持不变）。

        列名来自本仓储自己的 SELECT，顺序也沿用快照，因此不需要在建表语句变化时同步维护。
        """
        if not row:
            return
        columns = list(row.keys())
        placeholders = ",".join("?" * len(columns))
        async with self._connect() as db:
            await db.execute(
                f"INSERT OR REPLACE INTO history_records ({','.join(columns)}) VALUES ({placeholders})",
                tuple(row[c] for c in columns),
            )
            await db.commit()

    async def update_fields(
        self,
        record_id: str,
        status: TaskStatus | None = None,
        error_message: str | None = None,
        conflict_type: HistoryConflictType | None = None,
        conflict_data: dict | None = None,
        title: str | None = None,
        original_title: str | None = None,
        plot: str | None = None,
        poster_url: str | None = None,
        release_date: str | None = None,
        rating: float | None = None,
        tags: list[str] | None = None,
        season_number: int | None = None,
        episode_number: int | None = None,
        episode_title: str | None = None,
        episode_overview: str | None = None,
        episode_still_url: str | None = None,
        episode_air_date: str | None = None,
        duration_seconds: float | None = None,
        started_at: datetime | None = None,
        folder_path: str | None = None,
        timeout_step: str | None = None,
        timeout_seconds: int | None = None,
        tmdb_id: int | None = None,
    ) -> bool:
        updates = []
        params = []

        if folder_path is not None:
            updates.append("folder_path = ?")
            params.append(folder_path)
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if conflict_type is not None:
            updates.append("conflict_type = ?")
            params.append(conflict_type.value)
        if conflict_data is not None:
            updates.append("conflict_data = ?")
            params.append(json.dumps(conflict_data, ensure_ascii=False))
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if original_title is not None:
            updates.append("original_title = ?")
            params.append(original_title)
        if plot is not None:
            updates.append("plot = ?")
            params.append(plot)
        if poster_url is not None:
            updates.append("poster_url = ?")
            params.append(poster_url)
        if release_date is not None:
            updates.append("release_date = ?")
            params.append(release_date)
        if rating is not None:
            updates.append("rating = ?")
            params.append(rating)
        if tags is not None:
            updates.append("tags = ?")
            params.append(json.dumps(tags, ensure_ascii=False))
        if season_number is not None:
            updates.append("season_number = ?")
            params.append(season_number)
        if episode_number is not None:
            updates.append("episode_number = ?")
            params.append(episode_number)
        if episode_title is not None:
            updates.append("episode_title = ?")
            params.append(episode_title)
        if episode_overview is not None:
            updates.append("episode_overview = ?")
            params.append(episode_overview)
        if episode_still_url is not None:
            updates.append("episode_still_url = ?")
            params.append(episode_still_url)
        if episode_air_date is not None:
            updates.append("episode_air_date = ?")
            params.append(episode_air_date)
        if duration_seconds is not None:
            updates.append("duration_seconds = ?")
            params.append(duration_seconds)
        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at.isoformat())
        if timeout_step is not None:
            updates.append("timeout_step = ?")
            params.append(timeout_step)
        if timeout_seconds is not None:
            updates.append("timeout_seconds = ?")
            params.append(timeout_seconds)
        if tmdb_id is not None:
            updates.append("tmdb_id = ?")
            params.append(tmdb_id)

        if not updates:
            return False

        params.append(record_id)

        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE history_records SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()
            return cursor.rowcount > 0

    async def clear_records(self, before_days: int | None = None) -> int:
        """清空历史记录并级联删除关联的 scrape_jobs。"""
        async with self._connect() as db:
            if before_days is not None:
                cutoff = (datetime.now() - timedelta(days=before_days)).isoformat()
                # 先删除关联的 scrape_jobs
                await db.execute(
                    """DELETE FROM scrape_jobs WHERE id IN (
                        SELECT scrape_job_id FROM history_records
                        WHERE executed_at < ? AND scrape_job_id IS NOT NULL
                    )""",
                    (cutoff,),
                )
                cursor = await db.execute(
                    "DELETE FROM history_records WHERE executed_at < ?",
                    (cutoff,),
                )
            else:
                # 先删除关联的 scrape_jobs
                await db.execute(
                    """DELETE FROM scrape_jobs WHERE id IN (
                        SELECT scrape_job_id FROM history_records WHERE scrape_job_id IS NOT NULL
                    )"""
                )
                cursor = await db.execute("DELETE FROM history_records")
            await db.commit()
            return cursor.rowcount

    async def update_scrape_logs_json(self, record_id: str, scrape_logs_json: str) -> None:
        await self._execute(
            "UPDATE history_records SET scrape_logs = ? WHERE id = ?",
            (scrape_logs_json, record_id),
        )

    async def update_on_success(
        self,
        record_id: str,
        folder_path: str,
        duration_seconds: float,
        title: str | None,
        original_title: str | None,
        plot: str | None,
        poster_url: str | None,
        release_date: str | None,
        rating: float | None,
        tags_json: str | None,
        season_number: int | None,
        episode_number: int | None,
        episode_title: str | None,
        episode_overview: str | None,
        episode_still_url: str | None,
        episode_air_date: str | None,
    ) -> None:
        await self._execute(
            """UPDATE history_records SET
               status = ?, folder_path = ?, duration_seconds = ?, success_count = 1,
               title = ?, original_title = ?, plot = ?, poster_url = ?,
               release_date = ?, rating = ?, tags = ?,
               season_number = ?, episode_number = ?, episode_title = ?,
               episode_overview = ?, episode_still_url = ?, episode_air_date = ?
               WHERE id = ?""",
            (TaskStatus.SUCCESS.value, folder_path, duration_seconds,
             title, original_title, plot, poster_url,
             release_date, rating, tags_json,
             season_number, episode_number, episode_title,
             episode_overview, episode_still_url, episode_air_date, record_id),
        )
