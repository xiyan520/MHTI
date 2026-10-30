"""手动任务：未投递（跳过）的文件必须留下可查的记录。"""

from pathlib import Path

import aiosqlite
import pytest

import server.application.manual_job_service as manual_job_service_module
import server.application.scrape_job_service as scrape_job_service_module
from server.application.history_service import HistoryService
from server.application.manual_job_service import ManualJobService
from server.infrastructure.db import create_all_tables
from server.models.history import TaskStatus
from server.models.manual_job import LinkMode, ManualJobCreate, ManualJobStatus
from server.models.scrape_job import ScrapeJobCreate


async def _init_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


async def _records(db_path: Path) -> list[aiosqlite.Row]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT status, folder_path, error_message, manual_job_id FROM history_records"
            " ORDER BY executed_at"
        )
        return list(await cursor.fetchall())


async def _add_success_record(db_path: Path, job_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO history_records (
                id, task_name, folder_path, executed_at, status,
                total_files, success_count, failed_count, duration_seconds,
                manual_job_id
            ) VALUES ('rec-ok', '任务', '/src/b.mp4', '2026-10-30T22:00:02', 'success', 1, 1, 0, 1.0, ?)
            """,
            (job_id,),
        )
        await db.commit()


class _RejectingScrapeJobs:
    """模拟「文件已有处理中的任务」：create_job 返回 None。"""

    async def create_job(self, job: ScrapeJobCreate):  # noqa: ANN001, ANN201 - 测试替身
        return None


@pytest.mark.asyncio
async def test_skipped_file_creates_history_record(
    temp_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跳过只记统计不落记录，记录页就对不上；这里断言记录确实写入。"""
    await _init_db(temp_db)
    monkeypatch.setattr(manual_job_service_module, "_ensure_worker", lambda: None)
    monkeypatch.setattr(
        scrape_job_service_module, "ScrapeJobService", _RejectingScrapeJobs
    )

    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")

    service = ManualJobService(db_path=temp_db)
    created = await service.create_job(
        ManualJobCreate(
            scan_path=str(video),
            target_folder=str(tmp_path / "out"),
            link_mode=LinkMode.COPY,
        )
    )

    await manual_job_service_module._execute_job(service, created.id)

    records = await _records(temp_db)
    assert len(records) == 1
    assert records[0]["status"] == "skipped"
    assert records[0]["manual_job_id"] == created.id
    assert records[0]["folder_path"] == str(video)
    assert "已有处理中的刮削任务" in records[0]["error_message"]

    job = await service.get_job(created.id)
    assert job is not None
    assert job.status == ManualJobStatus.SUCCESS
    assert job.total_count == 1
    assert job.skip_count == 1
    assert job.success_count == 0

    # 记录页按 manual_job_id 过滤，走的就是这条查询
    listed, total = await HistoryService(db_path=temp_db).list_records(manual_job_id=created.id)
    assert total == 1
    assert [r.status for r in listed] == [TaskStatus.SKIPPED]


@pytest.mark.asyncio
async def test_skip_record_is_not_double_counted(
    temp_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跳过既有记录、又曾被算进「总数 - 已派发」，重算时不能加两次。"""
    await _init_db(temp_db)
    monkeypatch.setattr(manual_job_service_module, "_ensure_worker", lambda: None)
    monkeypatch.setattr(
        scrape_job_service_module, "ScrapeJobService", _RejectingScrapeJobs
    )

    video = tmp_path / "episode.mp4"
    video.write_bytes(b"video")

    service = ManualJobService(db_path=temp_db)
    created = await service.create_job(
        ManualJobCreate(
            scan_path=str(video),
            target_folder=str(tmp_path / "out"),
            link_mode=LinkMode.COPY,
        )
    )
    await manual_job_service_module._execute_job(service, created.id)

    # 反复刷新也不该把跳过数叠加
    await service.refresh_progress(created.id)
    await service.refresh_progress(created.id)

    job = await service.get_job(created.id)
    assert job is not None
    assert job.skip_count == 1
    assert job.success_count + job.skip_count + job.error_count == job.total_count


@pytest.mark.asyncio
async def test_mixed_batch_waits_for_dispatched_child(
    temp_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一批里既有跳过又有投递时，跳过立刻算完成，投递的要等子记录落地。"""
    await _init_db(temp_db)
    service = ManualJobService(db_path=temp_db)

    # 直接构造批次状态：2 个文件，1 个已投递、1 个跳过
    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO manual_jobs (
                scan_path, target_folder, link_mode, created_at, started_at,
                status, total_count, dispatched_count, success_count, skip_count, error_count
            ) VALUES ('/src', '/dst', 2, '2026-10-30T22:00:00', '2026-10-30T22:00:01',
                      'running', 2, 2, 0, 0, 0)
            """
        )
        job_id = int(cursor.lastrowid or 0)
        await db.execute(
            """
            INSERT INTO history_records (
                id, task_name, folder_path, executed_at, status,
                total_files, success_count, failed_count, duration_seconds, manual_job_id
            ) VALUES ('rec-skip', '跳过', '/src/a.mp4', '2026-10-30T22:00:02', 'skipped', 1, 0, 0, 0, ?)
            """,
            (job_id,),
        )
        await db.commit()

    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.RUNNING
    assert job.skip_count == 1

    await _add_success_record(temp_db, job_id)
    await service.refresh_progress(job_id)
    job = await service.get_job(job_id)
    assert job is not None
    assert job.status == ManualJobStatus.SUCCESS
    assert job.success_count == 1
    assert job.skip_count == 1
