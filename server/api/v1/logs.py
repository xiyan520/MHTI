"""日志管理 API 端点。"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from server.api.deps import get_log_service, require_auth
from server.domain.system.log_service import LogService
from server.models.log import (
    LogConfigUpdate,
    LogEntry,
    LogLevel,
    LogQuery,
)

router = APIRouter(
    prefix="/api/logs",
    tags=["logs"],
    dependencies=[Depends(require_auth)],
)


# ============================================================================
# Response Models
# ============================================================================


class LogListResponse:
    """日志列表响应。"""

    def __init__(self, items: list[LogEntry], total: int, page: int, page_size: int):
        self.items = items
        self.total = total
        self.page = page
        self.page_size = page_size
        self.total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0


# ============================================================================
# API Endpoints
# ============================================================================


@router.get("")
async def get_logs(
    level: Annotated[LogLevel | None, Query(description="日志级别筛选")] = None,
    logger: Annotated[str | None, Query(description="日志模块名筛选")] = None,
    search: Annotated[str | None, Query(description="消息内容搜索")] = None,
    start_time: Annotated[datetime | None, Query(description="开始时间")] = None,
    end_time: Annotated[datetime | None, Query(description="结束时间")] = None,
    page: Annotated[int, Query(ge=1, description="页码")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="每页数量")] = 50,
    log_service: LogService = Depends(get_log_service),
) -> dict:
    """
    获取日志列表。

    支持按级别、模块、时间范围和关键词筛选，支持分页。
    """
    query = LogQuery(
        level=level,
        logger=logger,
        search=search,
        start_time=start_time,
        end_time=end_time,
        limit=page_size,
        offset=(page - 1) * page_size,
    )

    items, total = await log_service.get_logs(query)

    return {
        "items": [
            {
                "id": item.id,
                "timestamp": item.timestamp.isoformat(),
                "level": item.level.value,
                "logger": item.logger,
                "message": item.message,
                "extra_data": item.extra_data,
                "request_id": item.request_id,
                "user_id": item.user_id,
            }
            for item in items
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if page_size > 0 else 0,
    }


@router.get("/stats")
async def get_log_stats(
    log_service: LogService = Depends(get_log_service),
) -> dict:
    """
    获取日志统计信息。

    返回总数、按级别统计、按模块统计、时间范围等。
    """
    stats = await log_service.get_stats()
    return {
        "total": stats.total,
        "by_level": stats.by_level,
        "by_logger": stats.by_logger,
        "oldest_entry": stats.oldest_entry.isoformat() if stats.oldest_entry else None,
        "newest_entry": stats.newest_entry.isoformat() if stats.newest_entry else None,
    }


@router.get("/loggers")
async def get_loggers(
    log_service: LogService = Depends(get_log_service),
) -> list[str]:
    """
    获取所有日志模块名称列表。

    用于前端筛选下拉框。
    """
    return await log_service.get_loggers()


@router.get("/config")
async def get_log_config(
    log_service: LogService = Depends(get_log_service),
) -> dict:
    """获取日志配置。"""
    config = await log_service.get_config()
    return {
        "log_level": config.log_level.value,
        "console_enabled": config.console_enabled,
        "file_enabled": config.file_enabled,
        "db_enabled": config.db_enabled,
        "max_file_size_mb": config.max_file_size_mb,
        "max_file_count": config.max_file_count,
        "db_retention_days": config.db_retention_days,
        "realtime_enabled": config.realtime_enabled,
    }


@router.put("/config")
async def update_log_config(
    update: LogConfigUpdate,
    log_service: LogService = Depends(get_log_service),
) -> dict:
    """
    更新日志配置。

    支持部分更新，只需传入要修改的字段。
    """
    config = await log_service.update_config(update)
    return {
        "log_level": config.log_level.value,
        "console_enabled": config.console_enabled,
        "file_enabled": config.file_enabled,
        "db_enabled": config.db_enabled,
        "max_file_size_mb": config.max_file_size_mb,
        "max_file_count": config.max_file_count,
        "db_retention_days": config.db_retention_days,
        "realtime_enabled": config.realtime_enabled,
    }


@router.delete("")
async def clear_logs(
    before: Annotated[datetime | None, Query(description="清理此时间之前的日志")] = None,
    level: Annotated[LogLevel | None, Query(description="只清理指定级别")] = None,
    log_service: LogService = Depends(get_log_service),
) -> dict:
    """
    清理日志。

    可按时间和级别筛选要清理的日志。
    """
    deleted = await log_service.clear_logs(before=before, level=level)
    return {"deleted": deleted, "message": f"已清理 {deleted} 条日志"}


@router.post("/cleanup")
async def cleanup_old_logs(
    log_service: LogService = Depends(get_log_service),
) -> dict:
    """
    清理过期日志。

    根据配置的保留天数自动清理旧日志。
    """
    deleted = await log_service.cleanup_old_logs()
    return {"deleted": deleted, "message": f"已清理 {deleted} 条过期日志"}


@router.get("/export")
async def export_logs(
    format: Annotated[str, Query(description="导出格式: json 或 csv")] = "json",
    start_time: Annotated[datetime | None, Query(description="开始时间")] = None,
    end_time: Annotated[datetime | None, Query(description="结束时间")] = None,
    limit: Annotated[int, Query(ge=1, le=100000, description="最大导出数量")] = 10000,
    log_service: LogService = Depends(get_log_service),
):
    """
    导出日志数据。

    支持 JSON 和 CSV 格式，可按时间范围筛选。
    """
    if format not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="格式必须是 json 或 csv")

    content = await log_service.export_logs(
        format=format,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
    )

    if format == "csv":
        return PlainTextResponse(
            content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=logs.csv"},
        )
    else:
        return PlainTextResponse(
            content,
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=logs.json"},
        )
