"""手动任务进度：计数以子记录为准，生命周期覆盖整批文件。"""

from pathlib import Path
from uuid import uuid4

import aiosqlite
import pytest

from server.application.manual_job_service import ManualJobService
from server.infrastructure.db import create_all_tables
from server.models.manual_job import ManualJobStatus


async def _init_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


async def _insert_job(
    db_path: Path,
    *,
    status: str = "running",
    total: int = 3,
    dispatched: int | None = 3,
    success: int = 0,
    skip: int = 0,
    error: int = 0,
    finished_at: str | None = None,
) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            """
            INSERT INTO manual_jobs (
                scan_path, target_folder, link_mode, created_at, started_at,
                status, total_count, dispatched_count,
                success_count, skip_count, error_count, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "/src",
                "/dst",
                2,
                "2026-10-30T22:00:00",
                "2026-10-30T22:00:01",
                status,
                total,
                dispatched,
                success,
                skip,
                error,
                finished_at,
            ),
        )
        await db.commit()
        return int(cursor.lastrowid or 0)


async def _add_record(db_path: Path, job_id: int, status: str) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO history_records (
                id, task_name, folder_path, executed_at, status,
                total_files, success_count, failed_count, duration_seconds,
                manual_job_id
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 1.0, ?)
            """,
            (
                f"rec-{uuid4().hex[:8]}",
                "任务",
                "/src/a.mp4",
                "2026-10-30T22:00:02",
                status,
                1 if status == "success" else 0,
                0 if status == "success" else 1,
                job_id,
            ),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_job_stays_running_until_children_finish(temp_db: Path) -> None:
    """投递完成不等于任务完成：子记录没落地前必须保持运行中。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)
    job_id = await _insert_job(temp_db, total=3, dispatched=3)

    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.RUNNING
    assert job.success_count == 0
    assert job.finished_at is None

    await _add_record(temp_db, job_id, "success")
    await _add_record(temp_db, job_id, "success")
    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.RUNNING
    assert job.success_count == 2

    await _add_record(temp_db, job_id, "success")
    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.SUCCESS
    assert job.success_count == 3
    assert job.finished_at is not None


@pytest.mark.asyncio
async def test_counts_are_recomputed_not_accumulated(temp_db: Path) -> None:
    """计数按记录重算：脏的成功数会被拉回真实值，不会越界。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)
    job_id = await _insert_job(temp_db, total=3, dispatched=3, success=5)
    for _ in range(3):
        await _add_record(temp_db, job_id, "success")

    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.success_count == 3
    assert job.success_count + job.skip_count + job.error_count == 3


@pytest.mark.asyncio
async def test_pending_action_record_holds_job_in_running(temp_db: Path) -> None:
    """有文件等待人工处理时，整批不算完成。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)
    job_id = await _insert_job(temp_db, total=2, dispatched=2)
    await _add_record(temp_db, job_id, "success")
    await _add_record(temp_db, job_id, "pending_action")

    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.RUNNING
    assert job.success_count == 1
    assert job.finished_at is None


@pytest.mark.asyncio
async def test_all_failed_marks_job_failed(temp_db: Path) -> None:
    """整批全失败时任务本身判失败，便于筛选。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)
    job_id = await _insert_job(temp_db, total=2, dispatched=2)
    await _add_record(temp_db, job_id, "failed")
    await _add_record(temp_db, job_id, "timeout")

    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.FAILED
    assert job.error_count == 2


@pytest.mark.asyncio
async def test_not_dispatched_files_count_as_skipped(temp_db: Path) -> None:
    """未派发的文件计入跳过，进度仍能收满到 100%。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)
    job_id = await _insert_job(temp_db, total=5, dispatched=3)
    for _ in range(3):
        await _add_record(temp_db, job_id, "success")

    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.SUCCESS
    assert job.skip_count == 2
    assert job.success_count + job.skip_count + job.error_count == job.total_count


@pytest.mark.asyncio
async def test_recovery_keeps_dispatched_job_alive(temp_db: Path) -> None:
    """已投递完的任务在重启后不再被直接判死，交给子任务推进。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)
    dispatched_job = await _insert_job(temp_db, total=2, dispatched=2)
    interrupted_job = await _insert_job(temp_db, total=2, dispatched=None)

    await service.recover_pending_jobs(start_workers=False)

    kept = await service.get_job(dispatched_job)
    failed = await service.get_job(interrupted_job)
    assert kept is not None and kept.status == ManualJobStatus.RUNNING
    assert failed is not None and failed.status == ManualJobStatus.FAILED


@pytest.mark.asyncio
async def test_legacy_overflow_repaired_on_schema_ensure(temp_db: Path) -> None:
    """历史脏数据（成功数超过总数）在迁移时按子记录修正。"""
    await _init_db(temp_db)
    job_id = await _insert_job(
        temp_db,
        status="success",
        total=9,
        dispatched=None,
        success=13,
        finished_at="2026-10-30T22:00:09",
    )
    for _ in range(9):
        await _add_record(temp_db, job_id, "success")

    service = ManualJobService(db_path=temp_db)
    await service._ensure_db()

    job = await service.get_job(job_id)
    assert job is not None
    assert job.success_count == 9
    assert job.success_count + job.skip_count + job.error_count == job.total_count


@pytest.mark.asyncio
async def test_worker_hook_refreshes_parent_manual_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """子任务落地后回写父任务进度；非手动来源不触发。"""
    from server.application import scrape_job_service as scrape_module
    from server.models.scrape_job import ScrapeJobSource

    calls: list[int] = []

    class FakeService:
        def __init__(self, source: ScrapeJobSource, source_id: int | None) -> None:
            self._source = source
            self._source_id = source_id

        async def get_job(self, job_id: str):  # noqa: ANN001, ANN201 - 测试替身
            job = type("FakeJob", (), {})()
            job.source = self._source
            job.source_id = self._source_id
            return job

    async def fake_refresh(self, job_id: int) -> None:  # noqa: ANN001 - 测试替身
        calls.append(job_id)

    monkeypatch.setattr(ManualJobService, "refresh_progress", fake_refresh)

    await scrape_module._refresh_manual_job_progress(
        FakeService(ScrapeJobSource.MANUAL, 42), "job-1"
    )
    await scrape_module._refresh_manual_job_progress(
        FakeService(ScrapeJobSource.WATCHER, None), "job-2"
    )

    assert calls == [42]
