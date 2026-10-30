"""Manual job service for managing manual scrape tasks."""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.manual_job_repository import ManualJobRepository
from server.models.manual_job import (
    JobSource,
    LinkMode,
    ManualJob,
    ManualJobAdvancedSettings,
    ManualJobCreate,
    ManualJobStatus,
)
from server.models.organize import OrganizeMode
from server.models.storage import StorageLocator, StorageProvider

logger = logging.getLogger(__name__)


def _link_mode_to_organize_mode(link_mode: LinkMode) -> OrganizeMode:
    """将 LinkMode 转换为 OrganizeMode"""
    mapping = {
        LinkMode.HARDLINK: OrganizeMode.HARDLINK,
        LinkMode.MOVE: OrganizeMode.MOVE,
        LinkMode.COPY: OrganizeMode.COPY,
        LinkMode.SYMLINK: OrganizeMode.SYMLINK,
    }
    return mapping.get(link_mode, OrganizeMode.MOVE)


def _build_file_locator_from_scan(
    scan_locator: StorageLocator,
    scanned_file,
    file_path: str,
) -> StorageLocator:
    """根据扫描结果构造单文件的 115 StorageLocator。"""
    return StorageLocator(
        provider=StorageProvider.P115,
        path=file_path,
        file_id=scanned_file.file_id or scan_locator.file_id,
        parent_id=scanned_file.parent_id or scan_locator.parent_id,
        is_dir=False,
    )


def _serialize_locator(locator: StorageLocator | None) -> str | None:
    """序列化存储定位信息。"""
    if locator is None:
        return None
    return json.dumps(locator.model_dump(mode="json"))


def _deserialize_locator(payload: str | None) -> StorageLocator | None:
    """反序列化存储定位信息。"""
    if not payload:
        return None
    try:
        return StorageLocator(**json.loads(payload))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None

# 任务队列
_job_queue: asyncio.Queue[int] = asyncio.Queue()
_worker_task: asyncio.Task | None = None


class ManualJobService:
    """Service for managing manual scrape jobs."""

    def __init__(self, db_path: Path | None = None):
        """Initialize manual job service."""
        self.db_path = db_path or DATABASE_PATH
        self._repo = ManualJobRepository(db_path)

    async def _ensure_db(self) -> None:
        """确保数据库目录存在并完成旧库列迁移。"""
        await self._repo.ensure_schema()

    async def create_job(self, job: ManualJobCreate) -> ManualJob:
        """Create a new manual job and add to queue."""
        await self._ensure_db()
        now = datetime.now()

        # 序列化高级设置
        advanced_settings_json = None
        if job.advanced_settings is not None:
            advanced_settings_json = json.dumps(job.advanced_settings.model_dump())
        scan_locator_json = _serialize_locator(job.scan_locator)
        target_locator_json = _serialize_locator(job.target_locator)
        metadata_locator_json = _serialize_locator(job.metadata_locator)

        job_id = await self._repo.insert_job(
            job,
            created_at=now.isoformat(),
            advanced_settings_json=advanced_settings_json,
            scan_locator_json=scan_locator_json,
            target_locator_json=target_locator_json,
            metadata_locator_json=metadata_locator_json,
        )

        created_job = ManualJob(
            id=job_id,
            scan_path=job.scan_path,
            target_folder=job.target_folder,
            metadata_dir=job.metadata_dir,
            scan_locator=job.scan_locator,
            target_locator=job.target_locator,
            metadata_locator=job.metadata_locator,
            allow_local_output=job.allow_local_output,
            link_mode=job.link_mode,
            delete_empty_parent=job.delete_empty_parent,
            config_reuse_id=job.config_reuse_id,
            source=job.source,
            advanced_settings=job.advanced_settings,
            created_at=now,
            status=ManualJobStatus.PENDING,
        )

        # 加入队列
        await _job_queue.put(job_id)
        # 确保 worker 在运行
        _ensure_worker()

        return created_job

    async def list_jobs(
        self,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        status: ManualJobStatus | None = None,
    ) -> tuple[list[ManualJob], int]:
        """List manual jobs with pagination, search and filter."""
        await self._ensure_db()

        rows, total = await self._repo.list_jobs(
            limit=limit, offset=offset, search=search, status=status
        )
        jobs = [self._row_to_job(row) for row in rows]
        return jobs, total

    async def get_job(self, job_id: int) -> ManualJob | None:
        """Get a manual job by ID."""
        await self._ensure_db()

        row = await self._repo.get_job_raw(job_id)
        if row is None:
            return None
        return self._row_to_job(row)

    async def delete_jobs(self, ids: list[int]) -> int:
        """Delete manual jobs by IDs.

        同时级联删除关联的刮削记录（history_records.manual_job_id）。
        """
        await self._ensure_db()

        return await self._repo.delete_jobs(ids)

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
        """Update job status and counts."""
        await self._ensure_db()

        await self._repo.update_job_status(
            job_id,
            status,
            started_at=started_at,
            finished_at=finished_at,
            success_count=success_count,
            skip_count=skip_count,
            error_count=error_count,
            total_count=total_count,
            dispatched_count=dispatched_count,
            error_message=error_message,
        )

    async def refresh_progress(self, job_id: int) -> None:
        """按子记录重算进度，并在整批处理完毕时收尾。

        手动任务的生命周期覆盖整批文件：投递完成只是中途状态，等每个子任务都
        落到终态、且成功/失败/跳过之和达到扫描总数时才结束。计数一律由
        history_records 重算，重试成功不会像旧的 ±1 累加那样把成功数顶过总数。

        Args:
            job_id: 手动任务 ID；不存在或不在运行中时直接返回。
        """
        await self._ensure_db()

        job = await self.get_job(job_id)
        if job is None or job.status != ManualJobStatus.RUNNING:
            return

        counts = await self._repo.count_records_by_status(job_id)
        success = counts.get("success", 0)
        error = sum(counts.get(status, 0) for status in ("failed", "timeout", "cancelled"))
        user_skipped = counts.get("skipped", 0)
        unresolved = sum(counts.get(status, 0) for status in ("running", "pending_action"))

        # 投递未结束时还无法确定有哪些文件被跳过，沿用投递阶段写入的值；
        # 投递结束后「总数 - 已派发」就是真实的跳过数。
        if job.dispatched_count is None:
            dispatch_skipped = job.skip_count
        else:
            dispatch_skipped = max(0, job.total_count - job.dispatched_count)
        skip = dispatch_skipped + user_skipped

        counters = {
            "success_count": success,
            "skip_count": skip,
            "error_count": error,
        }
        settled = (
            job.dispatched_count is not None
            and unresolved == 0
            and success + error + skip >= job.total_count
        )
        if not settled:
            await self.update_job_status(job_id, ManualJobStatus.RUNNING, **counters)
            return

        # 全部失败与「有成功/跳过」区分开，列表能直接筛出失败批次
        if success + skip == 0:
            await self.update_job_status(
                job_id,
                ManualJobStatus.FAILED,
                finished_at=datetime.now(),
                error_message="全部文件处理失败",
                **counters,
            )
        else:
            await self.update_job_status(
                job_id,
                ManualJobStatus.SUCCESS,
                finished_at=datetime.now(),
                **counters,
            )

    async def recover_pending_jobs(self, *, start_workers: bool = True) -> dict[str, int]:
        """启动恢复：收尾 running 孤儿，并把 pending 重新入队。

        与刮削任务同构：running 说明协程已随进程消失，标失败交给用户重跑；
        pending 还没开始，重放安全（重扫时会按文件路径与刮削任务去重）。

        Args:
            start_workers: 测试里传 False，避免真拉起队列消费者。
        """
        await self._ensure_db()

        summary = {"requeued": 0, "failed": 0}
        now = datetime.now()
        interrupted = "进程重启中断，请重新执行"

        for job_id in await self._repo.list_ids_by_status(ManualJobStatus.RUNNING):
            running_job = await self.get_job(job_id)
            # 已投递完毕的任务不在本进程里等结果：子任务恢复后照常执行，
            # 由它们落地时回写进度，重启不该把它判死。
            if running_job is not None and running_job.dispatched_count is not None:
                await self.refresh_progress(job_id)
                continue
            # 扫描/投递阶段中断：文件没有全部排进队列，任务无法自愈
            await self.update_job_status(
                job_id,
                ManualJobStatus.FAILED,
                finished_at=now,
                error_message=interrupted,
            )
            summary["failed"] += 1

        for job_id in await self._repo.list_ids_by_status(ManualJobStatus.PENDING):
            await _job_queue.put(job_id)
            summary["requeued"] += 1

        if summary["requeued"] and start_workers:
            _ensure_worker()

        if summary["requeued"] or summary["failed"]:
            logger.info(
                f"手动任务恢复: 重新入队 {summary['requeued']} 条，"
                f"中断标记失败 {summary['failed']} 条"
            )
        return summary

    def _row_to_job(self, row) -> ManualJob:
        """Convert database row to ManualJob."""
        # 兼容旧数据，source 可能不存在
        source_value = row["source"] if "source" in row.keys() else "manual"

        # 反序列化高级设置
        advanced_settings = None
        if "advanced_settings" in row.keys() and row["advanced_settings"]:
            try:
                settings_data = json.loads(row["advanced_settings"])
                advanced_settings = ManualJobAdvancedSettings(**settings_data)
            except (json.JSONDecodeError, ValueError):
                pass  # 解析失败则使用 None

        scan_locator = _deserialize_locator(
            row["scan_locator"] if "scan_locator" in row.keys() else None
        )
        target_locator = _deserialize_locator(
            row["target_locator"] if "target_locator" in row.keys() else None
        )
        metadata_locator = _deserialize_locator(
            row["metadata_locator"] if "metadata_locator" in row.keys() else None
        )
        allow_local_output = bool(
            row["allow_local_output"] if "allow_local_output" in row.keys() else 0
        )

        return ManualJob(
            id=row["id"],
            scan_path=row["scan_path"],
            target_folder=row["target_folder"],
            metadata_dir=row["metadata_dir"] or "",
            scan_locator=scan_locator,
            target_locator=target_locator,
            metadata_locator=metadata_locator,
            allow_local_output=allow_local_output,
            link_mode=LinkMode(row["link_mode"]),
            delete_empty_parent=bool(row["delete_empty_parent"]),
            config_reuse_id=row["config_reuse_id"],
            source=JobSource(source_value),
            advanced_settings=advanced_settings,
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            status=ManualJobStatus(row["status"]),
            success_count=row["success_count"],
            skip_count=row["skip_count"],
            error_count=row["error_count"],
            total_count=row["total_count"],
            dispatched_count=(
                row["dispatched_count"]
                if "dispatched_count" in row.keys() and row["dispatched_count"] is not None
                else None
            ),
            error_message=row["error_message"],
        )


def _ensure_worker() -> bool:
    """Ensure background worker is running.

    返回是否成功拉起 worker。worker 只能在事件循环里创建：没有运行中的循环时
    ``asyncio.create_task`` 会抛 RuntimeError。这里显式降级为「已入队但没有消费者」
    并留下日志，避免启动恢复或测试场景下静默失败。
    """
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return True
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("手动任务 worker 未启动：当前没有运行中的事件循环，队列将无人消费")
        return False
    _worker_task = loop.create_task(_job_worker())
    return True


async def _job_worker() -> None:
    """Background worker to process jobs from queue."""
    service = ManualJobService()

    while True:
        try:
            job_id = await _job_queue.get()
            await _execute_job(service, job_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Job worker error: {e}")


async def _execute_job(service: ManualJobService, job_id: int) -> None:
    """Execute a single manual job - 扫描文件并投递到刮削任务队列"""
    from server.application.scrape_job_service import ScrapeJobService
    from server.domain.media.file_service import FileService
    from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource

    job = await service.get_job(job_id)
    if job is None:
        logger.error(f"Job {job_id} not found")
        return

    logger.info(f"Starting manual job {job_id}: {job.scan_path}")

    # 更新状态为运行中
    started_at = datetime.now()
    await service.update_job_status(job_id, ManualJobStatus.RUNNING, started_at=started_at)

    try:
        # 扫描文件
        file_service = FileService()
        scan_path = Path(job.scan_path)

        is_p115_source = (
            job.scan_locator is not None
            and job.scan_locator.provider == StorageProvider.P115
        )

        if is_p115_source:
            scan_result = await file_service.scan_folder_async(
                job.scan_locator.path or job.scan_path,
                locator=job.scan_locator,
            )
            files = [f.path for f in scan_result]
        elif scan_path.is_file():
            files = [str(scan_path)]
        else:
            scan_result = file_service.scan_folder(job.scan_path)
            files = [f.path for f in scan_result]

        total_count = len(files)
        await service.update_job_status(job_id, ManualJobStatus.RUNNING, total_count=total_count)

        if total_count == 0:
            await service.update_job_status(
                job_id,
                ManualJobStatus.SUCCESS,
                finished_at=datetime.now(),
                error_message="没有找到视频文件",
            )
            return

        # 为每个文件创建刮削任务
        scrape_service = ScrapeJobService()
        organize_mode = _link_mode_to_organize_mode(job.link_mode)
        dispatched_count = 0
        skipped_count = 0

        for file_path in files:
            logger.info(f"手动任务 #{job_id} 投递文件: {file_path}")
            # 115 源文件需要构造 file_locator，携带 file_id 以便刮削下载
            file_locator = None
            if is_p115_source:
                scanned = next((f for f in scan_result if f.path == file_path), None)
                if scanned is not None:
                    file_locator = _build_file_locator_from_scan(
                        job.scan_locator,
                        scanned,
                        file_path,
                    )
            job_create = ScrapeJobCreate(
                file_path=file_path,
                output_dir=job.target_folder,
                metadata_dir=job.metadata_dir or None,
                file_locator=file_locator,
                output_locator=job.target_locator,
                metadata_locator=job.metadata_locator,
                allow_local_output=job.allow_local_output,
                link_mode=organize_mode,
                source=ScrapeJobSource.MANUAL,
                source_id=job_id,
                advanced_settings=job.advanced_settings,
            )
            created = await scrape_service.create_job(job_create)
            if created is not None:
                dispatched_count += 1
            else:
                skipped_count += 1
                logger.info(f"手动任务 #{job_id} 跳过（已有处理中任务）: {file_path}")

        # 投递结束只是中途状态：任务要等这批文件的子任务全部落地才算完成。
        # 这里先记下派发结果作为进度基准，再由 refresh_progress 结算。
        await service.update_job_status(
            job_id,
            ManualJobStatus.RUNNING,
            success_count=0,
            skip_count=skipped_count,
            error_count=0,
            dispatched_count=dispatched_count,
        )
        await service.refresh_progress(job_id)
        logger.info(
            f"Manual job {job_id} completed: {dispatched_count} dispatched, {skipped_count} skipped"
        )

    except Exception as e:
        logger.error(f"Manual job {job_id} failed: {e}")
        await service.update_job_status(
            job_id,
            ManualJobStatus.FAILED,
            finished_at=datetime.now(),
            error_message=str(e),
        )
        # 扫描阶段失败：创建一条失败历史记录，关联 manual_job_id，
        # 让用户在记录页能看到失败原因。
        try:
            from server.application.history_service import HistoryService
            from server.models.history import (
                HistoryConflictType,
                HistoryRecordCreate,
                TaskSource,
                TaskStatus,
            )
            duration = (datetime.now() - started_at).total_seconds()
            await HistoryService().create_record(HistoryRecordCreate(
                task_name=f"手动任务 #{job_id} 扫描失败",
                folder_path=job.scan_path,
                status=TaskStatus.FAILED,
                source=TaskSource.MANUAL,
                total_files=0,
                success_count=0,
                failed_count=1,
                duration_seconds=duration,
                manual_job_id=job_id,
                error_message=str(e),
                conflict_type=HistoryConflictType.NO_MATCH,
                conflict_data={
                    "output_dir": job.target_folder,
                    "metadata_dir": job.metadata_dir or None,
                    "link_mode": _link_mode_to_organize_mode(job.link_mode).value,
                    "parsed_title": None,
                    "parsed_season": None,
                    "parsed_episode": None,
                },
            ))
        except Exception as hist_err:
            logger.warning(f"Failed to create history record for failed manual job {job_id}: {hist_err}")


async def recover_pending_jobs() -> dict[str, int]:
    """应用启动时调用：把上次进程遗留的手动任务接回来。"""
    return await ManualJobService().recover_pending_jobs()


async def shutdown_workers() -> None:
    """取消手动任务 worker，避免进程退出时卡顿。"""
    global _worker_task
    if _worker_task is None:
        return
    if not _worker_task.done():
        _worker_task.cancel()
        await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    logger.info("Manual job worker cancelled")
