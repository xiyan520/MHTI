"""日志服务 - 管理应用日志的存储、查询和配置。"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any

from server.infrastructure.repositories.log_repository import LogRepository
from server.models.log import (
    LogConfig,
    LogConfigUpdate,
    LogEntry,
    LogEntryCreate,
    LogLevel,
    LogQuery,
    LogStats,
)

logger = logging.getLogger(__name__)


class LogService:
    """日志服务，提供日志存储、查询和配置管理功能。"""

    def __init__(self) -> None:
        """初始化日志服务。"""
        self._batch: list[dict[str, Any]] = []
        self._batch_lock = asyncio.Lock()
        self._batch_size = 50
        self._flush_interval = 5.0  # 秒
        self._flush_task: asyncio.Task | None = None
        self._running = False
        self._repo = LogRepository()

    async def start(self) -> None:
        """启动日志服务（启动定时刷新任务）。"""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.debug("LogService started")

    async def stop(self) -> None:
        """停止日志服务。"""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # 刷新剩余日志
        await self._flush()
        logger.debug("LogService stopped")

    async def _periodic_flush(self) -> None:
        """定时刷新日志到数据库。"""
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def _flush(self) -> None:
        """将缓冲的日志批量写入数据库。"""
        async with self._batch_lock:
            if not self._batch:
                return
            batch = self._batch.copy()
            self._batch.clear()

        if not batch:
            return

        try:
            rows = [
                (
                    entry["timestamp"],
                    entry["level"],
                    entry["logger"],
                    entry["message"],
                    json.dumps(entry["extra_data"]) if entry["extra_data"] else None,
                    entry["request_id"],
                    entry["user_id"],
                )
                for entry in batch
            ]
            await self._repo.insert_batch(rows)
        except Exception as e:
            logger.error(f"Failed to flush logs to database: {e}")

    async def add_log(self, entry: LogEntryCreate) -> None:
        """添加一条日志到缓冲区。"""
        async with self._batch_lock:
            self._batch.append({
                "timestamp": entry.timestamp.isoformat(),
                "level": entry.level.value,
                "logger": entry.logger,
                "message": entry.message,
                "extra_data": entry.extra_data,
                "request_id": entry.request_id,
                "user_id": entry.user_id,
            })

            # 达到批量大小时立即刷新
            if len(self._batch) >= self._batch_size:
                batch = self._batch.copy()
                self._batch.clear()

        # 在锁外执行数据库操作
        if len(batch) >= self._batch_size if 'batch' in dir() else False:
            await self._flush()

    async def batch_insert(self, entries: list[dict[str, Any]]) -> None:
        """批量插入日志条目。"""
        if not entries:
            return

        try:
            rows = [
                (
                    entry["timestamp"].isoformat() if isinstance(entry["timestamp"], datetime) else entry["timestamp"],
                    entry["level"],
                    entry["logger"],
                    entry["message"],
                    json.dumps(entry["extra_data"]) if entry.get("extra_data") else None,
                    entry.get("request_id"),
                    entry.get("user_id"),
                )
                for entry in entries
            ]
            await self._repo.insert_batch(rows)
        except Exception as e:
            logger.error(f"Failed to batch insert logs: {e}")

    async def get_logs(self, query: LogQuery) -> tuple[list[LogEntry], int]:
        """查询日志列表。"""
        rows, total = await self._repo.query_logs(query)

        items = [
            LogEntry(
                id=row[0],
                timestamp=datetime.fromisoformat(row[1]) if isinstance(row[1], str) else row[1],
                level=LogLevel(row[2]),
                logger=row[3],
                message=row[4],
                extra_data=json.loads(row[5]) if row[5] else None,
                request_id=row[6],
                user_id=row[7],
            )
            for row in rows
        ]

        return items, total

    async def get_stats(self) -> LogStats:
        """获取日志统计信息。"""
        raw = await self._repo.get_stats_rows()

        total = raw["total"]
        by_level = {row[0]: row[1] for row in raw["by_level_rows"]}
        by_logger = {row[0]: row[1] for row in raw["by_logger_rows"]}

        # 最早和最新记录
        row = raw["bounds"]
        oldest = datetime.fromisoformat(row[0]) if row and row[0] else None
        newest = datetime.fromisoformat(row[1]) if row and row[1] else None

        return LogStats(
            total=total,
            by_level=by_level,
            by_logger=by_logger,
            oldest_entry=oldest,
            newest_entry=newest,
        )

    async def get_config(self) -> LogConfig:
        """获取日志配置。"""
        row = await self._repo.get_config_row()
        if row:
            return LogConfig(
                log_level=LogLevel(row[0]),
                console_enabled=bool(row[1]),
                file_enabled=bool(row[2]),
                db_enabled=bool(row[3]),
                max_file_size_mb=row[4],
                max_file_count=row[5],
                db_retention_days=row[6],
                realtime_enabled=bool(row[7]),
            )
        return LogConfig()

    async def update_config(self, update: LogConfigUpdate) -> LogConfig:
        """更新日志配置。"""
        # 获取当前配置
        current = await self.get_config()

        # 合并更新
        new_config = LogConfig(
            log_level=update.log_level if update.log_level is not None else current.log_level,
            console_enabled=update.console_enabled if update.console_enabled is not None else current.console_enabled,
            file_enabled=update.file_enabled if update.file_enabled is not None else current.file_enabled,
            db_enabled=update.db_enabled if update.db_enabled is not None else current.db_enabled,
            max_file_size_mb=update.max_file_size_mb if update.max_file_size_mb is not None else current.max_file_size_mb,
            max_file_count=update.max_file_count if update.max_file_count is not None else current.max_file_count,
            db_retention_days=update.db_retention_days if update.db_retention_days is not None else current.db_retention_days,
            realtime_enabled=update.realtime_enabled if update.realtime_enabled is not None else current.realtime_enabled,
        )

        # 保存到数据库
        await self._repo.update_config_row(new_config)

        return new_config

    async def clear_logs(
        self,
        before: datetime | None = None,
        level: LogLevel | None = None,
    ) -> int:
        """清理日志。"""
        return await self._repo.delete_logs(before=before, level=level)

    async def cleanup_old_logs(self) -> int:
        """清理过期日志（根据配置的保留天数）。"""
        config = await self.get_config()
        cutoff = datetime.now() - timedelta(days=config.db_retention_days)
        return await self.clear_logs(before=cutoff)

    async def get_loggers(self) -> list[str]:
        """获取所有日志模块名称列表。"""
        return await self._repo.list_loggers()

    async def export_logs(
        self,
        format: str = "json",
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 10000,
    ) -> str:
        """导出日志数据。"""
        query = LogQuery(
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=0,
        )
        items, _ = await self.get_logs(query)

        if format == "csv":
            lines = ["timestamp,level,logger,message,request_id,user_id"]
            for item in items:
                # 转义 CSV 中的特殊字符
                message = item.message.replace('"', '""')
                lines.append(
                    f'"{item.timestamp.isoformat()}","{item.level.value}","{item.logger}","{message}","{item.request_id or ""}","{item.user_id or ""}"'
                )
            return "\n".join(lines)
        else:
            # JSON 格式
            return json.dumps(
                [
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
                ensure_ascii=False,
                indent=2,
            )
