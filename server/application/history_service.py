"""History service for managing scrape history records."""

import asyncio
import csv
import io
import json
import uuid
from datetime import datetime
from enum import Enum
from pathlib import Path

from server.application.undo_store import UndoSnapshot, UndoStore
from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.realtime import get_notifier
from server.infrastructure.repositories.history_repository import HistoryRepository
from server.infrastructure.repositories.scraped_file_repository import ScrapedFileRepository
from server.models.history import (
    HistoryConflictType,
    HistoryRecord,
    HistoryRecordCreate,
    HistoryRecordDetail,
    ScrapeLogStep,
    TaskSource,
    TaskStatus,
    UndoResponse,
)

# 内存日志缓存：record_id -> list[ScrapeLogStep]
_log_cache: dict[str, list[ScrapeLogStep]] = {}
# SSE 订阅者：record_id -> list[asyncio.Queue]
_log_subscribers: dict[str, list[asyncio.Queue]] = {}


def build_record_update_payload(**fields: object) -> dict:
    """把「本次实际写入的非 None 字段」拼成实时推送载荷。

    推送载荷必须与落库内容一致：列表页与详情页收到载荷后直接合并展示，
    载荷漏字段就会导致「后端已更新、页面等刷新才变」（历史上 duration_seconds
    与海报/简介等元数据就是这样被漏掉的）。
    """
    payload: dict = {}
    for key, value in fields.items():
        if value is None:
            continue
        # 枚举统一成字面值，datetime 统一成 ISO 字符串（与接口序列化一致）
        if isinstance(value, Enum):
            payload[key] = value.value
        elif isinstance(value, datetime):
            payload[key] = value.isoformat()
        else:
            payload[key] = value
    return payload


def _parse_optional_datetime(row, column: str) -> datetime | None:
    """读可空的时间列（旧库可能没有该列）。"""
    if column not in row.keys() or not row[column]:
        return None
    try:
        return datetime.fromisoformat(row[column])
    except (TypeError, ValueError):
        return None


def _row_get(row, column: str):
    """读可空列（旧库可能没有该列）。"""
    return row[column] if column in row.keys() else None


# folder_path 里「源文件 => 产物」的分隔符（刮削成功时才有产物）
_FOLDER_PATH_SEP = " => "


def build_record_folder_path(source_path: str | None, target_path: str | None) -> str:
    """拼记录的 folder_path：有产物时为「源文件 => 产物」，否则只存源文件。

    这条约定被三处依赖：列表页展示、`split_record_folder_path` 解析（删除文件 / 重新整理
    要从中取回源文件与产物路径）。写入与解析共用这一对函数，避免字符串格式再次漂移。
    """
    source = (source_path or "").strip()
    target = (target_path or "").strip()
    if source and target:
        return f"{source}{_FOLDER_PATH_SEP}{target}"
    return source or target


def split_record_folder_path(folder_path: str | None) -> tuple[str | None, str | None]:
    """把 folder_path 拆成 (源文件路径, 产物路径)，缺失的一侧为 None。"""
    text = (folder_path or "").strip()
    if not text:
        return None, None
    if _FOLDER_PATH_SEP not in text:
        return text, None
    source, _, target = text.partition(_FOLDER_PATH_SEP)
    return source.strip() or None, target.strip() or None


class HistoryService:
    """Service for managing scrape history records."""

    def __init__(self, db_path: Path | None = None):
        """Initialize history service."""
        self.db_path = db_path or DATABASE_PATH
        self._repo = HistoryRepository(db_path)
        self._scraped_repo = ScrapedFileRepository(db_path)
        # 最近一次可撤销操作（仅记录删除/清空，见 undo_store 说明）
        self._undo = UndoStore()

    async def _ensure_db(self) -> None:
        """确保数据库目录存在并完成旧库列迁移。"""
        await self._repo.ensure_schema()

    async def create_record(self, record: HistoryRecordCreate) -> HistoryRecord:
        """Create a new history record."""
        await self._ensure_db()

        record_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        # 序列化 conflict_data
        conflict_data_json = None
        if record.conflict_data is not None:
            conflict_data_json = json.dumps(record.conflict_data, ensure_ascii=False)

        # 序列化 scrape_logs
        scrape_logs_json = None
        if record.scrape_logs:
            scrape_logs_json = json.dumps(
                [log.model_dump() for log in record.scrape_logs],
                ensure_ascii=False
            )

        display_id = await self._repo.insert_record(
            record_id,
            record,
            executed_at=now.isoformat(),
            conflict_data_json=conflict_data_json,
            scrape_logs_json=scrape_logs_json,
            started_at=record.started_at,
        )

        result = HistoryRecord(
            id=record_id,
            display_id=display_id,
            task_name=record.task_name,
            folder_path=record.folder_path,
            executed_at=now,
            status=record.status,
            source=record.source,
            total_files=record.total_files,
            success_count=record.success_count,
            failed_count=record.failed_count,
            duration_seconds=record.duration_seconds,
            started_at=record.started_at,
            error_message=record.error_message,
            manual_job_id=record.manual_job_id,
            scrape_job_id=record.scrape_job_id,
        )

        # 发送 WebSocket 通知
        notifier = get_notifier()
        await notifier.notify_history_created(result.model_dump(mode="json"))

        return result

    async def list_records(
        self,
        limit: int = 100,
        offset: int = 0,
        manual_job_id: int | None = None,
        search: str | None = None,
        status: TaskStatus | None = None,
    ) -> tuple[list[HistoryRecord], int]:
        """List history records with pagination, search and status filter.

        优化策略：
        - 第一页使用快速模式，避免 COUNT 查询
        - 后续页面使用 COUNT 查询确保分页正确
        """
        await self._ensure_db()

        rows, total = await self._repo.list_records(
            limit=limit,
            offset=offset,
            manual_job_id=manual_job_id,
            search=search,
            status=status,
        )
        records = [self._row_to_record(row) for row in rows]
        return records, total

    async def get_record(self, record_id: str) -> HistoryRecordDetail | None:
        """Get a history record by ID with full details."""
        await self._ensure_db()

        row = await self._repo.get_record_raw(record_id)
        if row is None:
            return None

        return self._row_to_detail(row)

    async def get_existing_fingerprints(self, fingerprints: list[str]) -> set[str]:
        """
        查询已存在的文件指纹.

        Args:
            fingerprints: 要查询的指纹列表

        Returns:
            已存在的指纹集合
        """
        if not fingerprints:
            return set()

        await self._ensure_db()

        return await self._repo.get_fingerprints(fingerprints)

    async def delete_record(self, record_id: str) -> bool:
        """Delete a history record and its associated scrape job."""
        await self._ensure_db()

        # 先留快照再删：删除是不可逆的 DB 操作，撤销只能靠这份现场
        raw = await self._repo.get_record_raw(record_id)
        snapshot = (
            UndoSnapshot(
                kind="record_delete",
                message="已删除 1 条记录",
                records=[dict(raw)],
                files=await self._snapshot_files([record_id]),
            )
            if raw is not None
            else None
        )

        deleted = await self._repo.delete_record(record_id)

        # 发送 WebSocket 通知
        if deleted:
            if snapshot is not None:
                self._undo.push(snapshot)
            notifier = get_notifier()
            await notifier.notify_history_deleted(record_id)

        return deleted

    async def update_record(
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
        """Update a history record's status and conflict info."""
        await self._ensure_db()

        updated = await self._repo.update_fields(
            record_id,
            folder_path=folder_path,
            status=status,
            error_message=error_message,
            conflict_type=conflict_type,
            conflict_data=conflict_data,
            title=title,
            original_title=original_title,
            plot=plot,
            poster_url=poster_url,
            release_date=release_date,
            rating=rating,
            tags=tags,
            season_number=season_number,
            episode_number=episode_number,
            episode_title=episode_title,
            episode_overview=episode_overview,
            episode_still_url=episode_still_url,
            episode_air_date=episode_air_date,
            duration_seconds=duration_seconds,
            started_at=started_at,
            timeout_step=timeout_step,
            timeout_seconds=timeout_seconds,
            tmdb_id=tmdb_id,
        )

        # 发送 WebSocket 通知
        if updated:
            notifier = get_notifier()
            update_data = build_record_update_payload(
                folder_path=folder_path,
                status=status,
                error_message=error_message,
                conflict_type=conflict_type,
                conflict_data=conflict_data,
                title=title,
                original_title=original_title,
                plot=plot,
                poster_url=poster_url,
                release_date=release_date,
                rating=rating,
                tags=tags,
                season_number=season_number,
                episode_number=episode_number,
                episode_title=episode_title,
                episode_overview=episode_overview,
                episode_still_url=episode_still_url,
                episode_air_date=episode_air_date,
                duration_seconds=duration_seconds,
                started_at=started_at,
                timeout_step=timeout_step,
                timeout_seconds=timeout_seconds,
                tmdb_id=tmdb_id,
            )
            # 通知历史列表页（全局广播）
            await notifier.notify_history_updated(record_id, update_data)
            # 通知详情页订阅者（仅订阅了该记录的客户端）
            await notifier.notify_history_detail_update(record_id, update_data)

        return updated

    async def clear_records(self, before_days: int | None = None) -> int:
        """Clear history records and associated scrape jobs."""
        await self._ensure_db()

        # 与 clear_records 的筛选条件一致，保证快照就是即将被删掉的那些行
        rows = await self._repo.list_rows_raw(before_days)

        count = await self._repo.clear_records(before_days)

        # 发送 WebSocket 通知
        if count > 0:
            self._undo.push(
                UndoSnapshot(
                    kind="record_clear",
                    message=f"已清空 {count} 条记录",
                    records=rows,
                    files=await self._snapshot_files([row["id"] for row in rows]),
                )
            )
            notifier = get_notifier()
            await notifier.notify_history_cleared(count)

        return count

    async def _snapshot_files(self, record_ids: list[str]) -> list[dict]:
        """快照与被删记录关联的 scraped_files 行（恢复时一并插回）。"""
        rows: list[dict] = []
        for record_id in record_ids:
            rows.extend(
                dict(row) for row in await self._scraped_repo.list_by_history_record(record_id)
            )
        return rows

    async def undo_last(self) -> UndoResponse:
        """撤销最近一次可撤销操作（仅删除/清空记录）。

        物理删除的文件不在这里——磁盘上已经没有了，撤销无从谈起。
        """
        await self._ensure_db()

        snapshot = self._undo.pop()
        if snapshot is None:
            return UndoResponse(success=False, undone=False, message="没有可撤销的操作")

        for row in snapshot.files:
            await self._scraped_repo.restore_row(row)
        for row in snapshot.records:
            await self._repo.restore_row(row)

        notifier = get_notifier()
        await notifier.notify_history_restored(len(snapshot.records))

        return UndoResponse(
            success=True,
            undone=True,
            kind=snapshot.kind,
            restored=len(snapshot.records),
            message=f"已恢复 {len(snapshot.records)} 条记录",
        )

    async def export_csv(self) -> str:
        """Export history records as CSV."""
        records, _ = await self.list_records(limit=10000)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "ID", "任务名称", "文件夹路径", "执行时间", "状态",
            "总文件数", "成功数", "失败数", "耗时(秒)", "错误信息"
        ])

        for record in records:
            writer.writerow([
                record.id,
                record.task_name,
                record.folder_path,
                record.executed_at.isoformat(),
                record.status.value,
                record.total_files,
                record.success_count,
                record.failed_count,
                record.duration_seconds,
                record.error_message or "",
            ])

        return output.getvalue()

    def _row_to_record(self, row) -> HistoryRecord:
        """Convert database row to HistoryRecord."""
        # 兼容旧数据，source 可能不存在
        source_value = row["source"] if "source" in row.keys() else "manual"
        scrape_job_id = row["scrape_job_id"] if "scrape_job_id" in row.keys() else None
        return HistoryRecord(
            id=row["id"],
            display_id=row["display_id"] or 0,
            task_name=row["task_name"],
            folder_path=row["folder_path"],
            executed_at=datetime.fromisoformat(row["executed_at"]),
            status=TaskStatus(row["status"]),
            source=TaskSource(source_value),
            total_files=row["total_files"],
            success_count=row["success_count"],
            failed_count=row["failed_count"],
            duration_seconds=row["duration_seconds"],
            started_at=_parse_optional_datetime(row, "started_at"),
            error_message=row["error_message"],
            manual_job_id=row["manual_job_id"],
            scrape_job_id=scrape_job_id,
            title=row["title"],
            tmdb_id=_row_get(row, "tmdb_id"),
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            timeout_step=_row_get(row, "timeout_step"),
            timeout_seconds=_row_get(row, "timeout_seconds"),
        )

    def _row_to_detail(self, row) -> HistoryRecordDetail:
        """Convert database row to HistoryRecordDetail."""
        # 解析 tags JSON
        tags = []
        if row["tags"]:
            try:
                tags = json.loads(row["tags"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 scrape_logs JSON
        scrape_logs = []
        if row["scrape_logs"]:
            try:
                logs_data = json.loads(row["scrape_logs"])
                scrape_logs = [ScrapeLogStep(**log) for log in logs_data]
            except (json.JSONDecodeError, TypeError):
                pass

        # 解析 conflict_type
        conflict_type = None
        if row["conflict_type"]:
            try:
                conflict_type = HistoryConflictType(row["conflict_type"])
            except ValueError:
                pass

        # 解析 conflict_data JSON
        conflict_data = None
        if row["conflict_data"]:
            try:
                conflict_data = json.loads(row["conflict_data"])
            except (json.JSONDecodeError, TypeError):
                pass

        # 兼容旧数据，source 可能不存在
        source_value = row["source"] if "source" in row.keys() else "manual"
        scrape_job_id = row["scrape_job_id"] if "scrape_job_id" in row.keys() else None

        return HistoryRecordDetail(
            id=row["id"],
            display_id=row["display_id"] or 0,
            task_name=row["task_name"],
            folder_path=row["folder_path"],
            executed_at=datetime.fromisoformat(row["executed_at"]),
            status=TaskStatus(row["status"]),
            source=TaskSource(source_value),
            total_files=row["total_files"],
            success_count=row["success_count"],
            failed_count=row["failed_count"],
            duration_seconds=row["duration_seconds"],
            started_at=_parse_optional_datetime(row, "started_at"),
            error_message=row["error_message"],
            manual_job_id=row["manual_job_id"],
            scrape_job_id=scrape_job_id,
            title=row["title"],
            original_title=row["original_title"],
            plot=row["plot"],
            tags=tags,
            tmdb_id=_row_get(row, "tmdb_id"),
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            episode_title=row["episode_title"],
            episode_overview=row["episode_overview"],
            episode_still_url=row["episode_still_url"],
            episode_air_date=row["episode_air_date"],
            cover_url=row["cover_url"],
            poster_url=row["poster_url"],
            thumb_url=row["thumb_url"],
            release_date=row["release_date"],
            rating=row["rating"],
            votes=row["votes"],
            translator=row["translator"],
            scrape_logs=scrape_logs,
            conflict_type=conflict_type,
            conflict_data=conflict_data,
            timeout_step=_row_get(row, "timeout_step"),
            timeout_seconds=_row_get(row, "timeout_seconds"),
        )

    async def update_scrape_logs(
        self,
        record_id: str,
        logs: list[ScrapeLogStep],
    ) -> None:
        """更新刮削日志并通知订阅者"""
        # 更新内存缓存
        _log_cache[record_id] = logs

        # 通知所有订阅者（旧的 SSE 订阅者）
        if record_id in _log_subscribers:
            for queue in _log_subscribers[record_id]:
                try:
                    queue.put_nowait(logs)
                except asyncio.QueueFull:
                    pass

        # 持久化到数据库
        await self._ensure_db()
        scrape_logs_json = json.dumps(
            [log.model_dump() for log in logs],
            ensure_ascii=False
        )
        await self._repo.update_scrape_logs_json(record_id, scrape_logs_json)

        # 通过 WebSocket 推送日志更新（用于详情页实时刷新）
        notifier = get_notifier()
        await notifier.notify_history_detail_update(
            record_id,
            {"logs": [log.model_dump() for log in logs]}
        )

    async def subscribe_logs(self, record_id: str) -> asyncio.Queue:
        """订阅日志更新，返回一个队列用于接收更新"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        if record_id not in _log_subscribers:
            _log_subscribers[record_id] = []
        _log_subscribers[record_id].append(queue)

        # 如果缓存中有日志，立即发送
        if record_id in _log_cache:
            queue.put_nowait(_log_cache[record_id])

        return queue

    def unsubscribe_logs(self, record_id: str, queue: asyncio.Queue) -> None:
        """取消订阅日志更新"""
        if record_id in _log_subscribers:
            try:
                _log_subscribers[record_id].remove(queue)
                if not _log_subscribers[record_id]:
                    del _log_subscribers[record_id]
            except ValueError:
                pass

    def clear_log_cache(self, record_id: str) -> None:
        """清除日志缓存"""
        _log_cache.pop(record_id, None)

    async def flush_and_clear_log_cache(self, record_id: str) -> None:
        """保存缓存中的日志到数据库，然后清除缓存"""
        logs = _log_cache.get(record_id)
        if logs:
            await self.update_scrape_logs(record_id, logs)
        _log_cache.pop(record_id, None)

    async def update_record_on_success(
        self,
        record_id: str,
        folder_path: str,
        duration_seconds: float,
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
    ) -> None:
        """更新成功记录的额外字段"""
        await self._ensure_db()

        tags_json = json.dumps(tags, ensure_ascii=False) if tags else None

        await self._repo.update_on_success(
            record_id,
            folder_path,
            duration_seconds,
            title,
            original_title,
            plot,
            poster_url,
            release_date,
            rating,
            tags_json,
            season_number,
            episode_number,
            episode_title,
            episode_overview,
            episode_still_url,
            episode_air_date,
        )

        # 发送 WebSocket 通知
        notifier = get_notifier()
        update_data = build_record_update_payload(
            status=TaskStatus.SUCCESS,
            folder_path=folder_path,
            duration_seconds=duration_seconds,
            title=title,
            original_title=original_title,
            plot=plot,
            poster_url=poster_url,
            release_date=release_date,
            rating=rating,
            tags=tags,
            season_number=season_number,
            episode_number=episode_number,
            episode_title=episode_title,
            episode_overview=episode_overview,
            episode_still_url=episode_still_url,
            episode_air_date=episode_air_date,
        )
        # 通知历史列表页（全局广播）
        await notifier.notify_history_updated(record_id, update_data)
        # 通知详情页订阅者（仅订阅了该记录的客户端）
        await notifier.notify_history_detail_update(record_id, update_data)
