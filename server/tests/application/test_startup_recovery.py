"""进程重启后的任务恢复。

内存队列随进程消失：DB 里 pending 的任务没有任何消费者，running 的任务协程已经
不存在、也没有人收尾。这里覆盖恢复逻辑的三个断言点：

- pending 重新入队（不改状态，交给 worker 走正常流程）
- running 落成 failed，并同步收尾关联的历史记录
- 没有运行中的事件循环时，_ensure_worker 返回 False 而不是静默通过
"""

from datetime import datetime
from pathlib import Path

import aiosqlite
import pytest

import server.application.manual_job_service as manual_job_service_module
from server.application.history_service import HistoryService
from server.application.manual_job_service import (
    ManualJobService,
    _job_queue,
)
from server.application.manual_job_service import (
    _ensure_worker as ensure_manual_worker,
)
from server.application.scrape_job_service import (
    ScrapeJobService,
    _scrape_queue,
)
from server.application.scrape_job_service import (
    _ensure_worker as ensure_scrape_worker,
)
from server.infrastructure.db import create_all_tables
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.manual_job import ManualJobCreate, ManualJobStatus
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource, ScrapeJobStatus


async def _initialize_test_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


def _drain(queue) -> list:
    """取空队列里的 id（恢复只负责入队，消费由 worker 负责）。"""
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _scrape_create(name: str, root: Path) -> ScrapeJobCreate:
    return ScrapeJobCreate(
        file_path=str(root / name),
        output_dir=str(root / "out"),
        source=ScrapeJobSource.MANUAL,
    )


async def _insert_scrape_job(
    service: ScrapeJobService,
    job_id: str,
    name: str,
    root: Path,
) -> None:
    """直接落库建任务：走 create_job 会真入队并拉起 worker，这里不需要。"""
    await service._repo.insert_job(
        job_id,
        _scrape_create(name, root),
        created_at=datetime.now().isoformat(),
        advanced_settings_json=None,
        file_locator_json=None,
        output_locator_json=None,
        metadata_locator_json=None,
    )


@pytest.mark.asyncio
async def test_scrape_recovery_requeues_pending_and_fails_running(temp_db: Path) -> None:
    await _initialize_test_db(temp_db)
    service = ScrapeJobService(db_path=temp_db)
    history = HistoryService(db_path=temp_db)

    record = await history.create_record(
        HistoryRecordCreate(
            task_name="文件刮削任务 #run",
            folder_path=str(temp_db.parent / "A.mkv"),
            status=TaskStatus.RUNNING,
            total_files=1,
            success_count=0,
            failed_count=0,
            duration_seconds=0,
        )
    )

    # 一条 running，且已经把历史记录关联上（用户视角就是「一直处理中」的那条）
    await _insert_scrape_job(service, "running1", "A.mkv", temp_db.parent)
    await service.update_job("running1", history_record_id=record.id)
    await service.update_job("running1", status=ScrapeJobStatus.RUNNING, started_at=datetime.now())

    await _insert_scrape_job(service, "pend1", "B.mkv", temp_db.parent)
    await _insert_scrape_job(service, "pend2", "C.mkv", temp_db.parent)

    _drain(_scrape_queue)

    summary = await service.recover_pending_jobs(
        start_workers=False,
        history_service=history,
    )

    assert summary == {"requeued": 2, "failed": 1}
    assert sorted(_drain(_scrape_queue)) == ["pend1", "pend2"]

    interrupted = await service.get_job("running1")
    assert interrupted is not None
    assert interrupted.status == ScrapeJobStatus.FAILED
    assert "重启" in (interrupted.error_message or "")

    # 历史记录不能留在 running：列表页会一直转圈
    detail = await history.get_record(record.id)
    assert detail is not None
    assert detail.status == TaskStatus.FAILED
    assert "重启" in (detail.error_message or "")

    # pending 只是重新入队，状态保持原样，等 worker 自己走 running → 终态
    pending = await service.get_job("pend1")
    assert pending is not None
    assert pending.status == ScrapeJobStatus.PENDING


@pytest.mark.asyncio
async def test_scrape_recovery_on_empty_db_is_a_noop(temp_db: Path) -> None:
    await _initialize_test_db(temp_db)
    service = ScrapeJobService(db_path=temp_db)
    _drain(_scrape_queue)

    summary = await service.recover_pending_jobs(start_workers=False)

    assert summary == {"requeued": 0, "failed": 0}
    assert _drain(_scrape_queue) == []


@pytest.mark.asyncio
async def test_manual_recovery_requeues_pending_and_fails_running(
    temp_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _initialize_test_db(temp_db)
    # create_job 会拉起真实 worker，测试里换成空实现
    monkeypatch.setattr(manual_job_service_module, "_ensure_worker", lambda: True)

    service = ManualJobService(db_path=temp_db)
    pending_job = await service.create_job(
        ManualJobCreate(
            scan_path=str(temp_db.parent / "pending"),
            target_folder=str(temp_db.parent),
        )
    )
    running_job = await service.create_job(
        ManualJobCreate(
            scan_path=str(temp_db.parent / "running"),
            target_folder=str(temp_db.parent),
        )
    )
    await service.update_job_status(
        running_job.id, ManualJobStatus.RUNNING, started_at=datetime.now()
    )
    _drain(_job_queue)

    summary = await service.recover_pending_jobs(start_workers=False)

    assert summary == {"requeued": 1, "failed": 1}
    assert _drain(_job_queue) == [pending_job.id]

    interrupted = await service.get_job(running_job.id)
    assert interrupted is not None
    assert interrupted.status == ManualJobStatus.FAILED
    assert "重启" in (interrupted.error_message or "")


def test_ensure_worker_without_running_loop_returns_false() -> None:
    """同步上下文里没有事件循环：入队不会有消费者，必须如实返回 False。"""
    _drain(_scrape_queue)
    _drain(_job_queue)
    assert ensure_scrape_worker() is False
    assert ensure_manual_worker() is False
