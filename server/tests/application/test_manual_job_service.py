"""Unit tests for manual job and scrape job locator persistence."""

import json
from pathlib import Path

import aiosqlite
import pytest

import server.application.manual_job_service as manual_job_service_module
import server.application.scrape_job_service as scrape_job_service_module
from server.application.manual_job_service import ManualJobService
from server.application.scrape_job_service import ScrapeJobService
from server.infrastructure.db import create_all_tables
from server.models.manual_job import JobSource, LinkMode, ManualJobCreate
from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource
from server.models.storage import StorageLocator, StorageProvider


async def _initialize_test_db(db_path: Path) -> None:
    """Create the test schema in a temporary database."""
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


def _build_locator(
    *,
    path: str,
    file_id: str,
    parent_id: str | None = "0",
    is_dir: bool = True,
) -> StorageLocator:
    """Build a standard 115 locator for test assertions."""
    return StorageLocator(
        provider=StorageProvider.P115,
        path=path,
        file_id=file_id,
        parent_id=parent_id,
        is_dir=is_dir,
    )


@pytest.mark.asyncio
async def test_create_manual_job_persists_locators(
    temp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating a manual job should persist locator payloads and local output flag."""
    await _initialize_test_db(temp_db)
    monkeypatch.setattr(manual_job_service_module, "_ensure_worker", lambda: None)

    service = ManualJobService(db_path=temp_db)
    scan_locator = _build_locator(path="/115网盘/待整理", file_id="scan-root")
    target_locator = _build_locator(path="/115网盘/已整理", file_id="target-root")
    metadata_locator = _build_locator(path="/115网盘/元数据", file_id="meta-root")

    created = await service.create_job(
        ManualJobCreate(
            scan_path="/115网盘/待整理",
            target_folder="/115网盘/已整理",
            metadata_dir="/115网盘/元数据",
            link_mode=LinkMode.COPY,
            source=JobSource.MANUAL,
            scan_locator=scan_locator,
            target_locator=target_locator,
            metadata_locator=metadata_locator,
            allow_local_output=True,
        )
    )

    async with aiosqlite.connect(temp_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM manual_jobs WHERE id = ?",
            (created.id,),
        )
        row = await cursor.fetchone()

    assert row is not None
    assert json.loads(row["scan_locator"]) == scan_locator.model_dump(mode="json")
    assert json.loads(row["target_locator"]) == target_locator.model_dump(mode="json")
    assert json.loads(row["metadata_locator"]) == metadata_locator.model_dump(mode="json")
    assert row["allow_local_output"] == 1


@pytest.mark.asyncio
async def test_manual_job_get_job_restores_locators_from_db(temp_db: Path) -> None:
    """ManualJobService should deserialize locator fields when loading jobs from DB."""
    await _initialize_test_db(temp_db)
    service = ManualJobService(db_path=temp_db)

    scan_locator = _build_locator(path="/115网盘/待整理", file_id="scan-root")
    target_locator = _build_locator(path="/115网盘/已整理", file_id="target-root")
    metadata_locator = _build_locator(path="/115网盘/元数据", file_id="meta-root")

    async with aiosqlite.connect(temp_db) as db:
        cursor = await db.execute(
            """
            INSERT INTO manual_jobs (
                scan_path, target_folder, metadata_dir, link_mode, delete_empty_parent,
                config_reuse_id, source, advanced_settings, scan_locator, target_locator,
                metadata_locator, allow_local_output, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "/115网盘/待整理",
                "/115网盘/已整理",
                "/115网盘/元数据",
                LinkMode.MOVE.value,
                1,
                None,
                JobSource.MANUAL.value,
                None,
                json.dumps(scan_locator.model_dump(mode="json")),
                json.dumps(target_locator.model_dump(mode="json")),
                json.dumps(metadata_locator.model_dump(mode="json")),
                1,
                "2026-06-14T12:00:00",
                "pending",
            ),
        )
        await db.commit()
        job_id = cursor.lastrowid

    loaded = await service.get_job(job_id)

    assert loaded is not None
    assert loaded.scan_locator == scan_locator
    assert loaded.target_locator == target_locator
    assert loaded.metadata_locator == metadata_locator
    assert loaded.allow_local_output is True


@pytest.mark.asyncio
async def test_scrape_job_create_and_get_persist_locator_fields(
    temp_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ScrapeJobService should persist and restore locator fields."""
    await _initialize_test_db(temp_db)
    monkeypatch.setattr(scrape_job_service_module, "_ensure_worker", lambda: None)
    class FakeNotifier:
        async def notify_job_created(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(scrape_job_service_module, "get_notifier", lambda: FakeNotifier())

    service = ScrapeJobService(db_path=temp_db)
    file_locator = _build_locator(
        path="/115网盘/待整理/episode.mp4",
        file_id="file-001",
        parent_id="scan-root",
        is_dir=False,
    )
    output_locator = _build_locator(path="/115网盘/已整理", file_id="target-root")
    metadata_locator = _build_locator(path="/115网盘/元数据", file_id="meta-root")

    created = await service.create_job(
        ScrapeJobCreate(
            file_path="/115网盘/待整理/episode.mp4",
            output_dir="/115网盘/已整理",
            metadata_dir="/115网盘/元数据",
            source=ScrapeJobSource.MANUAL,
            source_id=7,
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=metadata_locator,
            allow_local_output=True,
        )
    )

    assert created is not None
    loaded = await service.get_job(created.id)

    assert loaded is not None
    assert loaded.file_locator == file_locator
    assert loaded.output_locator == output_locator
    assert loaded.metadata_locator == metadata_locator
    assert loaded.allow_local_output is True


@pytest.mark.asyncio
async def test_execute_job_forwards_locators_to_scrape_job_create(
    temp_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executing a manual job should forward locator payloads into scrape job creation."""
    await _initialize_test_db(temp_db)
    monkeypatch.setattr(manual_job_service_module, "_ensure_worker", lambda: None)

    created_jobs: list[ScrapeJobCreate] = []

    class FakeScrapeJobService:
        async def create_job(self, job: ScrapeJobCreate):
            created_jobs.append(job)
            return None

    monkeypatch.setattr(
        scrape_job_service_module,
        "ScrapeJobService",
        FakeScrapeJobService,
    )

    video_path = tmp_path / "episode.mp4"
    video_path.write_bytes(b"video")

    service = ManualJobService(db_path=temp_db)
    scan_locator = StorageLocator(
        provider=StorageProvider.LOCAL,
        path=str(video_path),
        is_dir=False,
    )
    target_locator = _build_locator(path="/115网盘/已整理", file_id="target-root")
    metadata_locator = _build_locator(path="/115网盘/元数据", file_id="meta-root")
    created = await service.create_job(
        ManualJobCreate(
            scan_path=str(video_path),
            target_folder="/115网盘/已整理",
            metadata_dir="/115网盘/元数据",
            link_mode=LinkMode.COPY,
            scan_locator=scan_locator,
            target_locator=target_locator,
            metadata_locator=metadata_locator,
            allow_local_output=True,
        )
    )

    await manual_job_service_module._execute_job(service, created.id)

    assert len(created_jobs) == 1
    forwarded = created_jobs[0]
    assert forwarded.file_path == str(video_path)
    assert forwarded.file_locator is None
    assert forwarded.output_locator == target_locator
    assert forwarded.metadata_locator == metadata_locator
    assert forwarded.allow_local_output is True


@pytest.mark.asyncio
async def test_execute_job_builds_file_locator_for_p115_source(
    temp_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual job with a 115 scan locator should propagate file_locator to scrape jobs."""
    await _initialize_test_db(temp_db)
    monkeypatch.setattr(manual_job_service_module, "_ensure_worker", lambda: None)

    created_jobs: list[ScrapeJobCreate] = []

    class FakeScrapeJobService:
        async def create_job(self, job: ScrapeJobCreate):
            created_jobs.append(job)
            return None

    monkeypatch.setattr(
        scrape_job_service_module,
        "ScrapeJobService",
        FakeScrapeJobService,
    )

    # Fake the FileService.scan_folder_async path used for 115 sources.
    from server.domain.media import file_service as file_service_module
    from server.models.file import ScannedFile

    scanned = [
        ScannedFile(
            filename="S01E01.mkv",
            path="/115网盘/待整理/S01E01.mkv",
            size=12345,
            extension=".mkv",
            file_id="300",
            parent_id="scan-root",
        ),
    ]

    class FakeFileService:
        async def scan_folder_async(self, folder_path, locator=None):
            assert locator is not None
            assert locator.provider == StorageProvider.P115
            return scanned

    monkeypatch.setattr(file_service_module, "FileService", FakeFileService)

    service = ManualJobService(db_path=temp_db)
    scan_locator = _build_locator(path="/115网盘/待整理", file_id="scan-root")
    target_locator = _build_locator(path="/115网盘/已整理", file_id="target-root")
    metadata_locator = _build_locator(path="/115网盘/元数据", file_id="meta-root")
    created = await service.create_job(
        ManualJobCreate(
            scan_path="/115网盘/待整理",
            target_folder="/115网盘/已整理",
            metadata_dir="/115网盘/元数据",
            link_mode=LinkMode.COPY,
            scan_locator=scan_locator,
            target_locator=target_locator,
            metadata_locator=metadata_locator,
            allow_local_output=True,
        )
    )

    await manual_job_service_module._execute_job(service, created.id)

    assert len(created_jobs) == 1
    forwarded = created_jobs[0]
    assert forwarded.file_path == "/115网盘/待整理/S01E01.mkv"
    assert forwarded.file_locator is not None
    assert forwarded.file_locator.provider == StorageProvider.P115
    assert forwarded.file_locator.file_id == "300"
    assert forwarded.file_locator.parent_id == "scan-root"
    assert forwarded.file_locator.is_dir is False
    assert forwarded.output_locator == target_locator
    assert forwarded.metadata_locator == metadata_locator
