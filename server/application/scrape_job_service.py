"""Scrape job service - 文件刮削任务服务"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from server.domain.media.fingerprint_service import calculate_fingerprint
from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.realtime import get_notifier
from server.infrastructure.repositories.scrape_job_repository import ScrapeJobRepository
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.organize import OrganizeMode
from server.models.scrape_job import (
    ScrapeJob,
    ScrapeJobCreate,
    ScrapeJobSource,
    ScrapeJobStatus,
)
from server.models.storage import StorageLocator

logger = logging.getLogger(__name__)

# 超时兜底步骤名：写进刮削日志时间轴的最后一步
TIMEOUT_STEP_NAME = "任务超时"


def resolve_timeout_step(logs: list) -> str | None:
    """超时前最后执行到哪一步：日志按执行顺序累积，最后一项就是它。

    步骤在开工前入列（如「搜索 TMDB」先 append 再发请求），所以末项是运行中或刚跑完的
    节点——两者无法从日志区分（末尾几步可能都已完成，超时卡在步骤之间的尾巴阶段，
    实测就有这种情况），因此文案只说「最后执行到」，不武断地说「卡在这一步」。
    已写入的超时兜底步骤本身不算节点。
    """
    for step in reversed(logs):
        if getattr(step, "name", None) != TIMEOUT_STEP_NAME:
            return step.name
    return None


def build_timeout_message(step_name: str | None, timeout_seconds: int) -> str:
    """超时原因：带上最后执行到的节点，列表页一行就能看出问题位置。"""
    if step_name:
        return f"任务超时（超过 {timeout_seconds} 秒，最后停在「{step_name}」节点）"
    return f"任务超时（超过 {timeout_seconds} 秒，尚未进入刮削步骤）"


def build_timeout_step(step_name: str | None, timeout_seconds: int, elapsed: float):
    """超时兜底步骤：把「停在哪、阈值多少、为什么不自动重试」写进时间轴。"""
    from server.models.history import ScrapeLogEntry, ScrapeLogLevel, ScrapeLogStep

    if step_name:
        first = ScrapeLogEntry(
            message=(
                f"超时节点: {step_name}"
                "（超时前最后执行到的步骤，其已产出的记录保留）"
            ),
            level=ScrapeLogLevel.ERROR,
        )
    else:
        first = ScrapeLogEntry(
            message="超时前尚未进入刮削步骤（卡在准备阶段）",
            level=ScrapeLogLevel.ERROR,
        )

    return ScrapeLogStep(
        name=TIMEOUT_STEP_NAME,
        completed=False,
        logs=[
            first,
            ScrapeLogEntry(
                message=(
                    f"超时阈值: {timeout_seconds} 秒"
                    "（系统设置 → 性能 → 任务超时，每次执行重新读取）"
                ),
                level=ScrapeLogLevel.WARNING,
            ),
            ScrapeLogEntry(
                message=(
                    f"本次已执行 {elapsed:.1f} 秒后中止；任务不会自动重试，"
                    "需要手动执行「重试刮削」"
                ),
                level=ScrapeLogLevel.WARNING,
            ),
        ],
    )


async def record_scrape_timeout(
    *,
    history_service,
    record_id: str,
    logs: list,
    step_name: str | None,
    timeout_seconds: int,
    elapsed: float,
) -> None:
    """超时现场的日志侧回写：追加一步超时兜底说明。

    刻意不改前面步骤的 completed：末步可能是真跑完了（超时卡在步骤之间的尾巴），
    改成「未完成」就是在说假话。卡住与否交给它自己的日志记录去说明。
    """
    logs.append(build_timeout_step(step_name, timeout_seconds, elapsed))
    await history_service.update_scrape_logs(record_id, logs)

# 任务队列和并发控制
_scrape_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_tasks: list[asyncio.Task] = []
_semaphore: asyncio.Semaphore | None = None
_current_threads: int = 0


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


class ScrapeJobService:
    """文件刮削任务服务"""

    def __init__(self, db_path: Path | None = None):
        """初始化服务"""
        self.db_path = db_path or DATABASE_PATH
        self._repo = ScrapeJobRepository(db_path)

    async def _ensure_db(self) -> None:
        """确保数据库目录存在并完成旧库列迁移。"""
        await self._repo.ensure_schema()

    async def get_pending_job_by_path(self, file_path: str) -> ScrapeJob | None:
        """根据文件路径获取仍在处理中的任务（pending/running/pending_action）。

        注意：不拦截 ``success`` 状态——用户应能重新整理已成功刮削的文件。
        """
        await self._ensure_db()

        row = await self._repo.get_pending_by_path(file_path)
        return self._row_to_job(row) if row else None

    async def get_pending_file_paths(self) -> set[str]:
        """获取所有待处理任务的文件路径"""
        await self._ensure_db()

        return await self._repo.get_pending_file_paths()

    async def create_job(self, job: ScrapeJobCreate, skip_duplicate_check: bool = False) -> ScrapeJob | None:
        """创建刮削任务并加入队列，如果已存在待处理任务则返回 None"""
        await self._ensure_db()

        # 去重检查：如果已有待处理任务，跳过创建
        if not skip_duplicate_check:
            existing = await self.get_pending_job_by_path(job.file_path)
            if existing:
                logger.info(f"文件已有待处理任务，跳过: {job.file_path}")
                return None

        job_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        # 序列化高级设置
        advanced_settings_json = None
        if job.advanced_settings is not None:
            advanced_settings_json = json.dumps(job.advanced_settings.model_dump())
        file_locator_json = _serialize_locator(job.file_locator)
        output_locator_json = _serialize_locator(job.output_locator)
        metadata_locator_json = _serialize_locator(job.metadata_locator)

        await self._repo.insert_job(
            job_id,
            job,
            created_at=now.isoformat(),
            advanced_settings_json=advanced_settings_json,
            file_locator_json=file_locator_json,
            output_locator_json=output_locator_json,
            metadata_locator_json=metadata_locator_json,
        )

        created_job = ScrapeJob(
            id=job_id,
            file_path=job.file_path,
            output_dir=job.output_dir,
            metadata_dir=job.metadata_dir,
            file_locator=job.file_locator,
            output_locator=job.output_locator,
            metadata_locator=job.metadata_locator,
            allow_local_output=job.allow_local_output,
            link_mode=job.link_mode,
            source=job.source,
            source_id=job.source_id,
            advanced_settings=job.advanced_settings,
            status=ScrapeJobStatus.PENDING,
            created_at=now,
        )

        # 加入队列
        await _scrape_queue.put(job_id)
        # 确保 worker 在运行
        _ensure_worker()

        # 发送 WebSocket 通知
        notifier = get_notifier()
        await notifier.notify_job_created(job_id, job.file_path, ScrapeJobStatus.PENDING.value)

        return created_job

    async def list_jobs(
        self,
        limit: int = 50,
        offset: int = 0,
        source: ScrapeJobSource | None = None,
        source_id: int | None = None,
        status: ScrapeJobStatus | None = None,
    ) -> tuple[list[ScrapeJob], int]:
        """列出刮削任务"""
        await self._ensure_db()

        rows, total = await self._repo.list_jobs(
            limit=limit, offset=offset, source=source, source_id=source_id, status=status
        )
        jobs = [self._row_to_job(row) for row in rows]
        return jobs, total

    async def get_job(self, job_id: str) -> ScrapeJob | None:
        """获取刮削任务"""
        await self._ensure_db()

        row = await self._repo.get_job_raw(job_id)
        if row is None:
            return None
        return self._row_to_job(row)

    async def update_job(
        self,
        job_id: str,
        status: ScrapeJobStatus | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error_message: str | None = None,
        history_record_id: str | None = None,
    ) -> None:
        """更新刮削任务"""
        await self._ensure_db()

        await self._repo.update_job(
            job_id,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            error_message=error_message,
            history_record_id=history_record_id,
        )

    async def recover_pending_jobs(
        self,
        *,
        start_workers: bool = True,
        history_service=None,
    ) -> dict[str, int]:
        """启动恢复：收尾 running 孤儿，并把 pending 重新入队。

        内存队列随进程消失，DB 里 pending 的任务重启后没有任何消费者；而 running
        的任务协程已经不存在，永远不会被收尾，前端只能一直转圈。两类分开处理：

        - running → failed：不自动重放。进程可能死在中途搬运上，盲目重跑会与
          半成品文件打架，交给用户确认后走「重试刮削」。
        - pending → 重新入队：还没开始执行，重放是安全的。

        Args:
            start_workers: 是否顺带拉起 worker。测试里传 False，避免真跑队列。
            history_service: 关联历史记录的服务，默认新建（测试可注入临时库实例）。

        Returns:
            {"requeued": 重新入队数, "failed": 标记失败的孤儿数}
        """
        await self._ensure_db()

        from server.models.history import TaskStatus

        if history_service is None:
            from server.application.history_service import HistoryService

            history_service = HistoryService()

        summary = {"requeued": 0, "failed": 0}
        now = datetime.now()
        interrupted = "进程重启中断，文件可能未完成搬运，请确认后重试"

        for job_id, history_record_id in await self._repo.list_running_with_record():
            await self.update_job(
                job_id,
                status=ScrapeJobStatus.FAILED,
                finished_at=now,
                error_message=interrupted,
            )
            if history_record_id:
                try:
                    await history_service.update_record(
                        history_record_id,
                        status=TaskStatus.FAILED,
                        error_message=interrupted,
                    )
                except Exception as exc:  # noqa: BLE001 - 记录收尾失败不该挡住任务恢复
                    logger.warning(f"收尾中断的历史记录失败 {history_record_id}: {exc}")
            summary["failed"] += 1

        for job_id in await self._repo.list_ids_by_status(ScrapeJobStatus.PENDING):
            await _scrape_queue.put(job_id)
            summary["requeued"] += 1

        if summary["requeued"] and start_workers:
            _ensure_worker()

        if summary["requeued"] or summary["failed"]:
            logger.info(
                f"刮削任务恢复: 重新入队 {summary['requeued']} 条，"
                f"中断标记失败 {summary['failed']} 条"
            )
        return summary

    async def delete_jobs(self, ids: list[str]) -> int:
        """删除刮削任务"""
        await self._ensure_db()

        return await self._repo.delete_jobs(ids)

    def _row_to_job(self, row) -> ScrapeJob:
        """转换数据库行到模型"""
        link_mode_value = row["link_mode"] if "link_mode" in row.keys() else None

        # 反序列化高级设置
        advanced_settings = None
        if "advanced_settings" in row.keys() and row["advanced_settings"]:
            try:
                settings_data = json.loads(row["advanced_settings"])
                advanced_settings = ManualJobAdvancedSettings(**settings_data)
            except (json.JSONDecodeError, ValueError):
                pass  # 解析失败则使用 None
        file_locator = _deserialize_locator(
            row["file_locator"] if "file_locator" in row.keys() else None
        )
        output_locator = _deserialize_locator(
            row["output_locator"] if "output_locator" in row.keys() else None
        )
        metadata_locator = _deserialize_locator(
            row["metadata_locator"] if "metadata_locator" in row.keys() else None
        )
        allow_local_output = bool(
            row["allow_local_output"] if "allow_local_output" in row.keys() else 0
        )

        return ScrapeJob(
            id=row["id"],
            file_path=row["file_path"],
            output_dir=row["output_dir"],
            metadata_dir=row["metadata_dir"],
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=metadata_locator,
            allow_local_output=allow_local_output,
            link_mode=OrganizeMode(link_mode_value) if link_mode_value else None,
            source=ScrapeJobSource(row["source"]),
            source_id=row["source_id"],
            advanced_settings=advanced_settings,
            status=ScrapeJobStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            error_message=row["error_message"],
            history_record_id=row["history_record_id"],
        )


def _ensure_worker() -> bool:
    """确保后台 worker 在运行，并根据配置调整并发数。

    返回是否成功把初始化排进事件循环。没有运行中的事件循环时（同步上下文、
    或在应用 shutdown 之后调用）入队不会有任何消费者，过去这里静默 pass，
    表现为「任务建成功但永远不动」，现在必须让调用方知道。
    """
    global _worker_tasks, _semaphore, _current_threads

    async def _init_workers():
        global _semaphore, _current_threads, _worker_tasks
        from server.domain.system.config_service import ConfigService

        try:
            config_service = ConfigService()
            system_config = await config_service.get_system_config()
            threads = system_config.scrape_threads
        except Exception as exc:  # noqa: BLE001 - 读配置失败不该让 worker 永远起不来
            logger.warning(f"读取刮削并发配置失败，按 1 个 worker 处理: {exc}")
            threads = 1

        # 如果并发数变化，重新初始化
        if threads != _current_threads:
            _current_threads = threads
            _semaphore = asyncio.Semaphore(threads)
            logger.info(f"刮削并发数设置为: {threads}")

        # 清理已完成的 worker
        _worker_tasks = [t for t in _worker_tasks if not t.done()]

        # 启动足够的 worker
        while len(_worker_tasks) < threads:
            task = asyncio.create_task(_scrape_worker())
            _worker_tasks.append(task)

    # 在事件循环中执行初始化
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("刮削 worker 未启动：当前没有运行中的事件循环，队列将无人消费")
        return False

    loop.create_task(_init_workers())
    return True


async def _refresh_manual_job_progress(service: ScrapeJobService, job_id: str) -> None:
    """子任务落地后回写所属手动任务的进度。

    手动任务的生命周期跨到子任务结束，父任务没有协程在等结果，因此进度由
    worker 在每个子任务执行完后统一刷新，而不是散落在各终态分支里。
    """
    try:
        job = await service.get_job(job_id)
        if job is None or job.source != ScrapeJobSource.MANUAL or not job.source_id:
            return
        from server.application.manual_job_service import ManualJobService

        await ManualJobService().refresh_progress(job.source_id)
    except Exception as exc:  # noqa: BLE001 - 进度回写失败不该影响刮削结果
        logger.warning(f"刷新手动任务进度失败 {job_id}: {exc}")


async def _scrape_worker() -> None:
    """后台 worker 处理刮削队列"""
    global _semaphore
    service = ScrapeJobService()

    while True:
        try:
            job_id = await _scrape_queue.get()
        except asyncio.CancelledError:
            break

        try:
            # 使用 Semaphore 控制并发
            if _semaphore:
                async with _semaphore:
                    await _execute_scrape_job(service, job_id)
            else:
                await _execute_scrape_job(service, job_id)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Scrape worker error: {e}")

        await _refresh_manual_job_progress(service, job_id)


async def _execute_scrape_job(service: ScrapeJobService, job_id: str) -> None:
    """执行单个刮削任务"""
    from server.application.history_service import HistoryService, build_record_folder_path
    from server.application.scraped_file_service import ScrapedFileService
    from server.bootstrap import get_scraper_service
    from server.domain.system.config_service import ConfigService
    from server.models.history import (
        HistoryConflictType,
        HistoryRecordCreate,
        TaskSource,
        TaskStatus,
    )
    from server.models.scraper import ScrapeRequest, ScrapeStatus

    job = await service.get_job(job_id)
    if job is None:
        logger.error(f"ScrapeJob {job_id} not found")
        return

    logger.info(f"Starting scrape job {job_id}: {job.file_path}")

    # 获取 WebSocket 通知器
    notifier = get_notifier()

    # 获取超时配置
    config_service = ConfigService()
    system_config = await config_service.get_system_config()
    timeout_seconds = system_config.task_timeout

    # 更新状态为运行中
    started_at = datetime.now()
    await service.update_job(job_id, status=ScrapeJobStatus.RUNNING, started_at=started_at)

    def elapsed_seconds() -> float:
        """本次执行已耗时：running 期间前端用 started_at 本地计时，落终态时写回真实值。"""
        return (datetime.now() - started_at).total_seconds()

    # 发送开始执行通知
    await notifier.notify_progress(job_id, "starting", 0, f"开始处理: {Path(job.file_path).name}")

    # 根据来源设置任务名称
    if job.source == ScrapeJobSource.WATCHER:
        task_name = f"文件刮削任务 #{job_id}"
        task_source = TaskSource.WATCHER
    else:
        task_name = f"文件刮削任务 #{job_id}"
        task_source = TaskSource.MANUAL

    history_service = HistoryService()
    scraped_file_service = ScrapedFileService()
    scraper = get_scraper_service()

    # 计算文件指纹
    file_fingerprint = calculate_fingerprint(job.file_path)

    # 创建历史记录
    history_record = await history_service.create_record(HistoryRecordCreate(
        task_name=task_name,
        folder_path=job.file_path,
        status=TaskStatus.RUNNING,
        source=task_source,
        total_files=1,
        success_count=0,
        failed_count=0,
        duration_seconds=0,
        started_at=started_at,  # 供前端实时计时（executed_at 是记录创建时间，重跑时不等同于本次开始）
        scrape_job_id=job_id,
        # 关联手动任务 ID，便于按任务聚合查询历史记录
        manual_job_id=job.source_id if job.source == ScrapeJobSource.MANUAL else None,
        file_fingerprint=file_fingerprint,
    ))
    record_id = history_record.id

    # 更新任务关联的历史记录ID
    await service.update_job(job_id, history_record_id=record_id)

    # 创建日志回调（同时记住最后执行到的节点：超时要写清停在哪一步）
    current_logs: list = []
    current_step_name: str | None = None

    async def on_log_update(logs):
        nonlocal current_logs, current_step_name
        if logs:
            current_logs = logs
            current_step_name = resolve_timeout_step(logs)
        await history_service.update_scrape_logs(record_id, logs)

    async def on_match_resolved(tmdb_id: int, season: int, episode: int):
        """匹配一确定就落库：这条记录超时/失败后仍能看出匹配的是哪部剧，重试直接沿用。"""
        await history_service.update_record(
            record_id,
            tmdb_id=tmdb_id,
            season_number=season,
            episode_number=episode,
        )

    try:
        request = ScrapeRequest(
            file_path=job.file_path,
            output_dir=job.output_dir,
            metadata_dir=job.metadata_dir,
            file_locator=job.file_locator,
            output_locator=job.output_locator,
            metadata_locator=job.metadata_locator,
            allow_local_output=job.allow_local_output,
            link_mode=job.link_mode,
            auto_select=True,
            advanced_settings=job.advanced_settings,
        )
        # 使用超时控制
        result = await asyncio.wait_for(
            scraper.scrape_file(
                request,
                on_log_update=on_log_update,
                on_match_resolved=on_match_resolved,
            ),
            timeout=timeout_seconds,
        )
        file_duration = elapsed_seconds()

        if result.status == ScrapeStatus.SUCCESS:
            # 更新历史记录为成功
            series = result.series_info
            episode = result.episode_info
            await history_service.update_record_on_success(
                record_id,
                folder_path=build_record_folder_path(job.file_path, result.dest_path or job.output_dir),
                duration_seconds=file_duration,
                title=series.name if series else None,
                original_title=series.original_name if series else None,
                plot=series.overview if series else None,
                poster_url=f"https://image.tmdb.org/t/p/w500{series.poster_path}" if series and series.poster_path else None,
                release_date=str(series.first_air_date) if series and series.first_air_date else None,
                rating=series.vote_average if series else None,
                tags=series.genres if series else None,
                season_number=result.parsed_season,
                episode_number=result.parsed_episode,
                episode_title=episode.name if episode else None,
                episode_overview=episode.overview if episode else None,
                episode_still_url=f"https://image.tmdb.org/t/p/w500{episode.still_path}" if episode and episode.still_path else None,
                episode_air_date=str(episode.air_date) if episode and episode.air_date else None,
            )

            # 登记产物：scraped_files 是记录详情的「删除文件」与「重新整理」查找源文件的依据
            # （设置页的「已刮削文件」面板已于 2026-09-27 移除）。此前只有重刮/重新整理的收尾会登记，
            # 扫描流程刮削的记录一条都没有，于是这两个功能对这些记录全都没数据可看。
            # 登记失败不影响刮削结果，故单独兜住异常。
            try:
                await scraped_file_service.register_output(
                    history_record_id=record_id,
                    source_path=result.file_path or job.file_path,
                    target_path=result.dest_path,
                    tmdb_id=result.selected_id,
                    season=result.parsed_season,
                    episode=result.parsed_episode,
                    title=series.name if series else None,
                )
            except Exception as exc:  # noqa: BLE001 - 登记是附带动作，不能拖垮刮削结果
                logger.warning(f"登记刮削产物失败 {record_id}: {exc}")

            await service.update_job(
                job_id,
                status=ScrapeJobStatus.SUCCESS,
                finished_at=datetime.now(),
            )
            # 发送完成通知
            await notifier.notify_completed(job_id, {
                "status": "success",
                "file_path": job.file_path,
                "dest_path": result.dest_path,
            })
        elif result.status == ScrapeStatus.NEED_SELECTION:
            # 需要用户选择
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "search_results": [r.model_dump(mode='json') for r in result.search_results] if result.search_results else [],
                "parsed_season": result.parsed_season,
                "parsed_episode": result.parsed_episode,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.NEED_SELECTION,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "need_selection", conflict_data)
        elif result.status == ScrapeStatus.NEED_SEASON_EPISODE:
            # 需要输入季集
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "tmdb_id": result.selected_id,
                "series_info": result.series_info.model_dump(mode='json') if result.series_info else None,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.NEED_SEASON_EPISODE,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "need_season_episode", conflict_data)
        elif result.status == ScrapeStatus.FILE_CONFLICT:
            # 文件冲突
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "tmdb_id": result.selected_id,
                "season": result.parsed_season,
                "episode": result.parsed_episode,
                "dest_path": result.dest_path,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.FILE_CONFLICT,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "file_conflict", conflict_data)
        elif result.status in (ScrapeStatus.NO_MATCH, ScrapeStatus.SEARCH_FAILED, ScrapeStatus.API_FAILED):
            # 需要手动输入 TMDB ID
            conflict_type_map = {
                ScrapeStatus.NO_MATCH: HistoryConflictType.NO_MATCH,
                ScrapeStatus.SEARCH_FAILED: HistoryConflictType.SEARCH_FAILED,
                ScrapeStatus.API_FAILED: HistoryConflictType.API_FAILED,
            }
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "parsed_title": result.parsed_title,
                "parsed_season": result.parsed_season,
                "parsed_episode": result.parsed_episode,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=conflict_type_map[result.status],
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "need_tmdb_id", conflict_data)
        elif result.status == ScrapeStatus.EMBY_CONFLICT:
            # Emby 冲突
            conflict_data = {
                "output_dir": job.output_dir,
                "metadata_dir": job.metadata_dir,
                "link_mode": job.link_mode.value if job.link_mode else None,
                "tmdb_id": result.selected_id,
                "season": result.parsed_season,
                "episode": result.parsed_episode,
                "series_info": result.series_info.model_dump(mode='json') if result.series_info else None,
                "emby_message": result.message,
                "emby_conflict": result.emby_conflict.model_dump(mode='json') if result.emby_conflict else None,
            }
            await history_service.update_record(
                record_id,
                status=TaskStatus.PENDING_ACTION,
                error_message=result.message,
                conflict_type=HistoryConflictType.EMBY_CONFLICT,
                conflict_data=conflict_data,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.PENDING_ACTION,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送需要用户操作通知
            await notifier.notify_need_action(job_id, "emby_conflict", conflict_data)
        else:
            # 其他失败
            await history_service.update_record(
                record_id,
                status=TaskStatus.FAILED,
                error_message=result.message,
                duration_seconds=elapsed_seconds(),
            )
            await service.update_job(
                job_id,
                status=ScrapeJobStatus.FAILED,
                finished_at=datetime.now(),
                error_message=result.message,
            )
            # 发送失败通知
            await notifier.notify_failed(job_id, result.message or "未知错误")

        # 清理日志缓存
        history_service.clear_log_cache(record_id)

    except asyncio.TimeoutError:
        timeout_msg = build_timeout_message(current_step_name, timeout_seconds)
        logger.warning(
            f"ScrapeJob {job_id} timeout after {timeout_seconds}s at step "
            f"{current_step_name or 'unknown'}: {job.file_path}"
        )
        # 先补上超时现场（被中断的步骤 + 兜底步骤），再写终态字段
        try:
            await record_scrape_timeout(
                history_service=history_service,
                record_id=record_id,
                logs=current_logs,
                step_name=current_step_name,
                timeout_seconds=timeout_seconds,
                elapsed=elapsed_seconds(),
            )
        except Exception as log_error:
            # 日志回写失败不能让超时状态丢失
            logger.error(f"ScrapeJob {job_id} timeout log write failed: {log_error}")
        await history_service.update_record(
            record_id,
            status=TaskStatus.TIMEOUT,
            error_message=timeout_msg,
            duration_seconds=elapsed_seconds(),
            timeout_step=current_step_name,
            timeout_seconds=timeout_seconds,
        )
        await service.update_job(
            job_id,
            status=ScrapeJobStatus.TIMEOUT,
            finished_at=datetime.now(),
            error_message=timeout_msg,
        )
        # 发送失败通知
        await notifier.notify_failed(job_id, timeout_msg)
        await history_service.flush_and_clear_log_cache(record_id)

    except Exception as e:
        error_msg = str(e) or repr(e) or type(e).__name__
        logger.error(f"Error scraping {job.file_path}: {error_msg}")
        await history_service.update_record(
            record_id,
            status=TaskStatus.FAILED,
            error_message=error_msg,
            duration_seconds=elapsed_seconds(),
        )
        await service.update_job(
            job_id,
            status=ScrapeJobStatus.FAILED,
            finished_at=datetime.now(),
            error_message=error_msg,
        )
        # 发送失败通知
        await notifier.notify_failed(job_id, error_msg)
        history_service.clear_log_cache(record_id)

    logger.info(f"ScrapeJob {job_id} completed with status: {job.status}")


async def shutdown_workers() -> None:
    """取消所有刮削 worker 任务，避免进程退出时卡顿。

    worker 阻塞在队列 ``get()`` 上，进程退出前必须显式取消，否则
    uvorn 会在 lifespan shutdown 阶段等待它们直到超时。
    """
    global _worker_tasks
    if not _worker_tasks:
        return
    for task in _worker_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*_worker_tasks, return_exceptions=True)
    _worker_tasks = []
    logger.info("Scrape workers cancelled")


async def recover_pending_jobs() -> dict[str, int]:
    """应用启动时调用：把上次进程遗留的刮削任务接回来。

    组合根（``server.main`` lifespan）在服务容器初始化之后调用一次。失败不应该
    挡住应用启动——恢复是尽力而为的补偿动作。
    """
    return await ScrapeJobService().recover_pending_jobs()
