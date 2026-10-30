"""manual_jobs 表仓储。"""

from __future__ import annotations

from datetime import datetime

import aiosqlite

from server.infrastructure.repositories.base import BaseRepository
from server.models.manual_job import ManualJobCreate, ManualJobStatus

_MANUAL_JOBS_DDL = """
    CREATE TABLE IF NOT EXISTS manual_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_path TEXT NOT NULL,
        target_folder TEXT NOT NULL,
        metadata_dir TEXT DEFAULT '',
        link_mode INTEGER NOT NULL DEFAULT 2,
        delete_empty_parent INTEGER DEFAULT 1,
        config_reuse_id INTEGER,
        source TEXT DEFAULT 'manual',
        advanced_settings TEXT,
        scan_locator TEXT,
        target_locator TEXT,
        metadata_locator TEXT,
        allow_local_output INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        success_count INTEGER DEFAULT 0,
        skip_count INTEGER DEFAULT 0,
        error_count INTEGER DEFAULT 0,
        total_count INTEGER DEFAULT 0,
        dispatched_count INTEGER,
        error_message TEXT
    )
"""


class ManualJobRepository(BaseRepository):
    """手动任务表访问。"""

    async def _ensure_custom_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_MANUAL_JOBS_DDL)

    async def ensure_schema(self) -> None:
        """兼容旧数据库：补齐缺失列（幂等）。"""
        async with self._connect() as db:
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN metadata_dir TEXT DEFAULT ''")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN source TEXT DEFAULT 'manual'")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN advanced_settings TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN scan_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN target_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN metadata_locator TEXT")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN allow_local_output INTEGER DEFAULT 0")
            except Exception:
                pass  # 列已存在
            try:
                await db.execute("ALTER TABLE manual_jobs ADD COLUMN dispatched_count INTEGER")
            except Exception:
                pass  # 列已存在
            await db.commit()

            # 一次性修复：旧口径下「重试 +1」会把成功数累加到超过总数
            # （实测 13/9 = 144%）。终态任务的计数一律以子记录为准，
            # 仅在发现越界时才回写，避免每次启动都重算全表。
            try:
                await db.execute(
                    """
                    UPDATE manual_jobs
                       SET success_count = (
                               SELECT COUNT(*) FROM history_records h
                                WHERE h.manual_job_id = manual_jobs.id
                                  AND h.status = 'success'
                           ),
                           error_count = (
                               SELECT COUNT(*) FROM history_records h
                                WHERE h.manual_job_id = manual_jobs.id
                                  AND h.status IN ('failed', 'timeout', 'cancelled')
                           )
                     WHERE total_count > 0
                       AND success_count + skip_count + error_count > total_count
                    """
                )
                await db.commit()
            except Exception:
                pass  # history_records 尚未建表时跳过，
                      # 下次启动还会再试

    async def insert_job(
        self,
        data: ManualJobCreate,
        *,
        created_at: str,
        advanced_settings_json: str | None,
        scan_locator_json: str | None,
        target_locator_json: str | None,
        metadata_locator_json: str | None,
    ) -> int:
        """插入手动任务，返回自增 job_id。"""
        async with self._connect() as db:
            cursor = await db.execute(
                """
                INSERT INTO manual_jobs
                (scan_path, target_folder, metadata_dir, link_mode, delete_empty_parent,
                 config_reuse_id, source, advanced_settings, scan_locator, target_locator,
                 metadata_locator, allow_local_output, created_at, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.scan_path,
                    data.target_folder,
                    data.metadata_dir,
                    data.link_mode.value,
                    1 if data.delete_empty_parent else 0,
                    data.config_reuse_id,
                    data.source.value,
                    advanced_settings_json,
                    scan_locator_json,
                    target_locator_json,
                    metadata_locator_json,
                    1 if data.allow_local_output else 0,
                    created_at,
                    ManualJobStatus.PENDING.value,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        status: ManualJobStatus | None = None,
    ) -> tuple[list[aiosqlite.Row], int]:
        conditions = []
        params = []

        if search:
            conditions.append("(scan_path LIKE ? OR target_folder LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])

        if status:
            conditions.append("status = ?")
            params.append(status.value)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        async with self._connect() as db:
            # Get total count
            cursor = await db.execute(
                f"SELECT COUNT(*) as count FROM manual_jobs {where_clause}",
                params,
            )
            row = await cursor.fetchone()
            total = row["count"] if row else 0

            # Get records
            cursor = await db.execute(
                f"""
                SELECT * FROM manual_jobs {where_clause}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            )
            rows = await cursor.fetchall()

        return list(rows), total

    async def get_job_raw(self, job_id: int) -> aiosqlite.Row | None:
        return await self._fetch_one(
            "SELECT * FROM manual_jobs WHERE id = ?", (job_id,)
        )

    async def count_records_by_status(self, job_id: int) -> dict[str, int]:
        """按状态统计该任务的子记录数（进度重算的数据来源）。

        每个被投递的文件都会在执行开始时建一条 history_records，
        因此记录状态就是这批文件的真实处理结果。
        """
        if job_id is None:
            return {}
        try:
            rows = await self._fetch_all(
                "SELECT status, COUNT(*) AS count FROM history_records "
                "WHERE manual_job_id = ? GROUP BY status",
                (job_id,),
            )
        except Exception:
            return {}
        return {str(row["status"]): int(row["count"]) for row in rows}

    async def list_ids_by_status(self, status: ManualJobStatus) -> list[int]:
        """按状态列出任务 id（启动恢复重放入口）。"""
        rows = await self._fetch_all(
            "SELECT id FROM manual_jobs WHERE status = ? ORDER BY created_at",
            (status.value,),
        )
        return [int(row[0]) for row in rows]

    async def delete_jobs(self, ids: list[int]) -> int:
        """删除任务并级联删除关联的刮削记录（history_records.manual_job_id）。"""
        if not ids:
            return 0

        placeholders = ",".join("?" * len(ids))
        async with self._connect() as db:
            # 级联删除关联的刮削记录
            await db.execute(
                f"DELETE FROM history_records WHERE manual_job_id IN ({placeholders})",
                ids,
            )
            cursor = await db.execute(
                f"DELETE FROM manual_jobs WHERE id IN ({placeholders})",
                ids,
            )
            await db.commit()
            return cursor.rowcount

    async def update_job_status(
        self,
        job_id: int,
        status: ManualJobStatus,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        success_count: int | None = None,
        skip_count: int | None = None,
        error_count: int | None = None,
        total_count: int | None = None,
        dispatched_count: int | None = None,
        error_message: str | None = None,
    ) -> None:
        updates = ["status = ?"]
        params = [status.value]

        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at.isoformat())
        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(finished_at.isoformat())
        if success_count is not None:
            updates.append("success_count = ?")
            params.append(success_count)
        if skip_count is not None:
            updates.append("skip_count = ?")
            params.append(skip_count)
        if error_count is not None:
            updates.append("error_count = ?")
            params.append(error_count)
        if total_count is not None:
            updates.append("total_count = ?")
            params.append(total_count)
        if dispatched_count is not None:
            updates.append("dispatched_count = ?")
            params.append(dispatched_count)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        params.append(job_id)

        async with self._connect() as db:
            await db.execute(
                f"UPDATE manual_jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            await db.commit()
