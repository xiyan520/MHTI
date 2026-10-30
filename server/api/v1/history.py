"""History API endpoints."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from server.api.deps import (
    get_config_service,
    get_history_service,
    get_manual_job_service,
    get_scrape_job_service,
    get_scraped_file_service,
    require_auth,
)
from server.application.history_actions import HistoryScrapeActions
from server.application.history_files import HistoryFileService
from server.application.history_service import HistoryService
from server.application.manual_job_service import ManualJobService
from server.application.scrape_job_service import ScrapeJobService
from server.application.scraped_file_service import ScrapedFileService
from server.domain.system.config_service import ConfigService
from server.models.history import (
    HistoryConflictType,
    HistoryFileDeleteRequest,
    HistoryFileDeleteResponse,
    HistoryFileListResponse,
    HistoryListResponse,
    HistoryRecord,
    HistoryRecordCreate,
    HistoryRecordDetail,
    TaskStatus,
    UndoResponse,
)
from server.models.organize import OrganizeMode
from server.models.scraper import ScrapeByIdRequest

router = APIRouter(prefix="/api/history", tags=["history"], dependencies=[Depends(require_auth)])


@router.get("", response_model=HistoryListResponse)
async def list_records(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    manual_job_id: int | None = Query(None),
    search: str | None = Query(None, description="搜索名称、文件夹"),
    status: TaskStatus | None = Query(None, description="状态筛选"),
    history_service: HistoryService = Depends(get_history_service),
) -> HistoryListResponse:
    """List history records with pagination, search and status filter."""
    records, total = await history_service.list_records(
        limit=limit,
        offset=offset,
        manual_job_id=manual_job_id,
        search=search,
        status=status,
    )
    return HistoryListResponse(records=records, total=total)


@router.post("", response_model=HistoryRecord)
async def create_record(
    record: HistoryRecordCreate,
    history_service: HistoryService = Depends(get_history_service),
) -> HistoryRecord:
    """Create a new history record."""
    return await history_service.create_record(record)


@router.post("/undo", response_model=UndoResponse)
async def undo_last(
    history_service: HistoryService = Depends(get_history_service),
) -> UndoResponse:
    """撤销最近一次可撤销操作（删除单条记录 / 清空记录）。

    物理删除的文件不在这里——磁盘上已经没有了，撤销无从谈起。
    放在 /{record_id} 之前声明，避免被详情路由吃掉。
    """
    return await history_service.undo_last()


@router.get("/export")
async def export_records(
    history_service: HistoryService = Depends(get_history_service),
) -> PlainTextResponse:
    """Export history records as CSV."""
    csv_content = await history_service.export_csv()
    return PlainTextResponse(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=history.csv"},
    )


@router.delete("")
async def clear_records(
    before_days: int | None = Query(None, ge=1, description="Clear records older than N days"),
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Clear history records."""
    deleted = await history_service.clear_records(before_days=before_days)
    return {"success": True, "deleted": deleted, "message": f"已删除 {deleted} 条记录"}


@router.get("/{record_id}", response_model=HistoryRecordDetail)
async def get_record(
    record_id: str,
    history_service: HistoryService = Depends(get_history_service),
) -> HistoryRecordDetail:
    """Get a history record by ID."""
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


@router.get("/{record_id}/logs/stream")
async def stream_logs(
    record_id: str,
    history_service: HistoryService = Depends(get_history_service),
):
    """SSE 端点：实时推送刮削日志"""
    # 验证记录存在
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    async def event_generator():
        queue = await history_service.subscribe_logs(record_id)
        try:
            # 先发送当前日志
            if record.scrape_logs:
                yield {
                    "event": "logs",
                    "data": json.dumps(
                        [log.model_dump() for log in record.scrape_logs],
                        ensure_ascii=False
                    ),
                }

            # 持续监听更新
            while True:
                try:
                    logs = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": "logs",
                        "data": json.dumps(
                            [log.model_dump() for log in logs],
                            ensure_ascii=False
                        ),
                    }
                except asyncio.TimeoutError:
                    # 发送心跳保持连接
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            history_service.unsubscribe_logs(record_id, queue)

    return EventSourceResponse(event_generator())


@router.delete("/{record_id}")
async def delete_record(
    record_id: str,
    history_service: HistoryService = Depends(get_history_service),
) -> dict:
    """Delete a history record."""
    deleted = await history_service.delete_record(record_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"success": True, "message": "记录已删除"}


class ResolveConflictRequest(BaseModel):
    """请求模型：处理冲突"""

    conflict_type: HistoryConflictType
    # NEED_SELECTION: 选择的 TMDB ID
    tmdb_id: int | None = None
    # NEED_SEASON_EPISODE: 季/集号
    season: int | None = None
    episode: int | None = None
    # FILE_CONFLICT: 处理方式
    file_action: str | None = None  # "overwrite" | "skip" | "rename"


def get_history_file_service(
    history_service: HistoryService = Depends(get_history_service),
    scraped_file_service: ScrapedFileService = Depends(get_scraped_file_service),
    config_service: ConfigService = Depends(get_config_service),
    scrape_job_service: ScrapeJobService = Depends(get_scrape_job_service),
    manual_job_service: ManualJobService = Depends(get_manual_job_service),
) -> HistoryFileService:
    """构造记录文件用例（依赖经 FastAPI 覆盖链注入）。"""
    return HistoryFileService(
        history_service,
        scraped_file_service,
        config_service,
        scrape_job_service,
        manual_job_service,
    )


def get_history_scrape_actions(
    history_service: HistoryService = Depends(get_history_service),
    scrape_job_service: ScrapeJobService = Depends(get_scrape_job_service),
    scraped_file_service: ScrapedFileService = Depends(get_scraped_file_service),
    files: HistoryFileService = Depends(get_history_file_service),
) -> HistoryScrapeActions:
    """构造历史刮削用例（依赖经 FastAPI 覆盖链注入）。"""
    return HistoryScrapeActions(history_service, scrape_job_service, scraped_file_service, files)


def _build_scrape_request(
    record: HistoryRecordDetail,
    *,
    tmdb_id: int,
    season: int,
    episode: int,
    output_dir: str | None,
    metadata_dir: str | None,
    link_mode: OrganizeMode | None,
    locators: dict | None = None,
    skip_emby_check: bool = False,
) -> ScrapeByIdRequest:
    """构建刮削请求（重试与各冲突分支共用）。"""
    kwargs: dict = {
        "file_path": record.folder_path,
        "tmdb_id": tmdb_id,
        "season": season,
        "episode": episode,
        "output_dir": output_dir,
        "metadata_dir": metadata_dir,
        "link_mode": link_mode,
    }
    if skip_emby_check:
        kwargs["skip_emby_check"] = True
    if locators:
        kwargs.update(locators)
    return ScrapeByIdRequest(**kwargs)


@router.put("/{record_id}/resolve")
async def resolve_conflict(
    record_id: str,
    request: ResolveConflictRequest,
    history_service: HistoryService = Depends(get_history_service),
    actions: HistoryScrapeActions = Depends(get_history_scrape_actions),
) -> dict:
    """处理待处理的冲突记录"""
    # 获取记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    if record.status != TaskStatus.PENDING_ACTION:
        raise HTTPException(status_code=400, detail="该记录不需要处理")

    if record.conflict_type != request.conflict_type:
        raise HTTPException(status_code=400, detail="冲突类型不匹配")

    output_dir = record.conflict_data.get("output_dir") if record.conflict_data else None
    metadata_dir = record.conflict_data.get("metadata_dir") if record.conflict_data else None
    # 从 conflict_data 恢复 link_mode
    link_mode_value = record.conflict_data.get("link_mode") if record.conflict_data else None
    link_mode = OrganizeMode(link_mode_value) if link_mode_value else None

    # 恢复 locator（支持 115 等云端文件重试）
    locators = await actions.restore_locators(record)

    # 根据冲突类型处理
    if request.conflict_type == HistoryConflictType.NEED_SELECTION:
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请选择 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        # 获取选中剧集名称
        selected_name = f"TMDB ID: {request.tmdb_id}"
        if record.conflict_data and record.conflict_data.get("search_results"):
            for r in record.conflict_data["search_results"]:
                if r.get("id") == request.tmdb_id:
                    selected_name = r.get("name", selected_name)
                    break

        user_log = f"用户选择了「{selected_name}」S{request.season:02d}E{request.episode:02d}"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
        )
        return await actions.execute_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type == HistoryConflictType.NEED_SEASON_EPISODE:
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        user_log = f"用户选择了 S{request.season:02d}E{request.episode:02d}"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
        )
        return await actions.execute_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type == HistoryConflictType.FILE_CONFLICT:
        if request.file_action not in ("overwrite", "skip", "rename"):
            raise HTTPException(status_code=400, detail="无效的处理方式")

        if request.file_action == "skip":
            await history_service.update_record(
                record_id, status=TaskStatus.SKIPPED, error_message="用户跳过"
            )
            # 跳过同样是终态，所属手动任务可能因此收尾
            if record.manual_job_id:
                await ManualJobService().refresh_progress(record.manual_job_id)
            return {"success": True, "message": "已跳过"}

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        action_text = "覆盖" if request.file_action == "overwrite" else "重命名"
        user_log = f"用户选择了{action_text}文件"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=tmdb_id,
            season=record.conflict_data.get("season", 1) if record.conflict_data else 1,
            episode=record.conflict_data.get("episode", 1) if record.conflict_data else 1,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
        )
        return await actions.execute_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type in (HistoryConflictType.NO_MATCH, HistoryConflictType.SEARCH_FAILED, HistoryConflictType.API_FAILED):
        # 手动输入 TMDB ID 的情况
        if request.tmdb_id is None:
            raise HTTPException(status_code=400, detail="请输入 TMDB ID")
        if request.season is None or request.episode is None:
            raise HTTPException(status_code=400, detail="请提供季/集号")

        user_log = f"用户手动输入 TMDB ID: {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=request.tmdb_id,
            season=request.season,
            episode=request.episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=locators,
        )
        return await actions.execute_scrape_and_update(record_id, scrape_request, user_log)

    elif request.conflict_type == HistoryConflictType.EMBY_CONFLICT:
        # Emby 冲突处理
        if request.file_action == "skip":
            await history_service.update_record(
                record_id, status=TaskStatus.SKIPPED, error_message="用户跳过（Emby 已存在）"
            )
            # 跳过同样是终态，所属手动任务可能因此收尾
            if record.manual_job_id:
                await ManualJobService().refresh_progress(record.manual_job_id)
            return {"success": True, "message": "已跳过"}

        tmdb_id = record.conflict_data.get("tmdb_id") if record.conflict_data else None
        if tmdb_id is None:
            raise HTTPException(status_code=400, detail="缺少 TMDB ID")

        # 获取季/集号（用户可能选择了其他季/集）
        season = request.season if request.season is not None else (record.conflict_data.get("season", 1) if record.conflict_data else 1)
        episode = request.episode if request.episode is not None else (record.conflict_data.get("episode", 1) if record.conflict_data else 1)

        if request.file_action == "force":
            user_log = f"用户强制继续刮削 S{season:02d}E{episode:02d}（忽略 Emby 冲突）"
        else:
            user_log = f"用户选择刮削为 S{season:02d}E{episode:02d}"

        scrape_request = _build_scrape_request(
            record,
            tmdb_id=tmdb_id,
            season=season,
            episode=episode,
            output_dir=output_dir,
            metadata_dir=metadata_dir,
            link_mode=link_mode,
            locators=None,
            skip_emby_check=True,  # 跳过 Emby 检查
        )
        return await actions.execute_scrape_and_update(record_id, scrape_request, user_log)

    raise HTTPException(status_code=400, detail="未知的冲突类型")


class RetryRequest(BaseModel):
    """请求模型：重试刮削"""

    tmdb_id: int  # TMDB ID
    season: int  # 季号
    episode: int  # 集号


@router.post("/{record_id}/retry")
async def retry_scrape(
    record_id: str,
    request: RetryRequest,
    history_service: HistoryService = Depends(get_history_service),
    actions: HistoryScrapeActions = Depends(get_history_scrape_actions),
) -> dict:
    """重刮记录（可按指定 TMDB ID / 季 / 集）。

    状态限制只保留「正在处理中」：这条接口同时承担「内联 TMDB ID 直接重刮」，
    用于修正匹配错的成功记录——只允许 failed/timeout/cancelled 时无法修正错误匹配。
    已经跑完的记录允许重刮，代价是可能产生重复产物（旧产物由后端先清掉），
    所以前端必须二次确认。

    输入文件不由调用方指定：后端按「源文件优先、源文件不存在就用整理后的产物」
    自行解析（resolve_input），否则拿 folder_path 那串「源 => 产物」当路径会直接报文件不存在。
    """
    # 1. 获取并验证记录
    record = await history_service.get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 2. 正在处理中的记录不允许并发重刮
    if record.status == TaskStatus.RUNNING:
        raise HTTPException(status_code=400, detail="该记录正在处理中，请稍后再试")

    # 3. 从 conflict_data 恢复原始参数
    conflict_data = record.conflict_data or {}
    output_dir = conflict_data.get("output_dir")
    metadata_dir = conflict_data.get("metadata_dir")
    link_mode_value = conflict_data.get("link_mode")
    link_mode = OrganizeMode(link_mode_value) if link_mode_value else None

    # 4. 构建刮削请求（恢复 locator 以支持 115 等云端文件）
    user_log = f"用户手动重试: TMDB ID {request.tmdb_id}, S{request.season:02d}E{request.episode:02d}"
    locators = await actions.restore_locators(record)

    scrape_request = _build_scrape_request(
        record,
        tmdb_id=request.tmdb_id,
        season=request.season,
        episode=request.episode,
        output_dir=output_dir,
        metadata_dir=metadata_dir,
        link_mode=link_mode,
        locators=locators,
    )

    # 5. 执行刮削（输入解析与旧产物清理在用例层完成）
    return await actions.execute_scrape_and_update(
        record_id, scrape_request, user_log, resolve_input=True
    )


@router.get("/{record_id}/files", response_model=HistoryFileListResponse)
async def list_record_files(
    record_id: str,
    files: HistoryFileService = Depends(get_history_file_service),
) -> HistoryFileListResponse:
    """记录关联的源文件与刮削产物清单（删除确认弹窗据此列出具体路径）。"""
    try:
        return await files.list_files(record_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="记录不存在")


@router.post("/{record_id}/files/delete", response_model=HistoryFileDeleteResponse)
async def delete_record_files(
    record_id: str,
    request: HistoryFileDeleteRequest,
    files: HistoryFileService = Depends(get_history_file_service),
) -> HistoryFileDeleteResponse:
    """物理删除记录的源文件 / 刮削产物（不可恢复，前端必须二次确认）。"""
    try:
        return await files.delete_files(record_id, request.scope)
    except LookupError:
        raise HTTPException(status_code=404, detail="记录不存在")


@router.post("/{record_id}/reorganize")
async def reorganize_record(
    record_id: str,
    actions: HistoryScrapeActions = Depends(get_history_scrape_actions),
) -> dict:
    """用已存元数据重跑整理（不请求 TMDB）。"""
    return await actions.reorganize_from_metadata(record_id)
