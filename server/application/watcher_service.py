"""Folder watcher service for monitoring and auto-scraping."""

import asyncio
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from watchdog.events import FileCreatedEvent, FileMovedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from server.domain.media.file_service import SUPPORTED_VIDEO_EXTENSIONS as VIDEO_EXTENSIONS
from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.watcher_repository import WatcherRepository
from server.models.watcher import (
    DetectedFile,
    WatchedFolder,
    WatchedFolderCreate,
    WatchedFolderUpdate,
    WatcherMode,
    WatcherNotification,
    WatcherStatus,
    WatcherStatusResponse,
)

logger = logging.getLogger(__name__)

# 监听的视频扩展名与 file_service.SUPPORTED_VIDEO_EXTENSIONS 保持一致


class VideoFileHandler(FileSystemEventHandler):
    """处理视频文件事件的处理器"""

    def __init__(self, folder: WatchedFolder, callback: Callable[[str, WatchedFolder], None]):
        self.folder = folder
        self.callback = callback

    def _is_video_file(self, path: str) -> bool:
        """检查是否为视频文件"""
        ext = Path(path).suffix.lower()
        return ext in VIDEO_EXTENSIONS

    def on_created(self, event: FileCreatedEvent) -> None:
        """文件创建事件"""
        if event.is_directory:
            return
        if self._is_video_file(event.src_path):
            logger.info(f"检测到新文件: {event.src_path}")
            self.callback(event.src_path, self.folder)

    def on_moved(self, event: FileMovedEvent) -> None:
        """文件移动事件（重命名）"""
        if event.is_directory:
            return
        if self._is_video_file(event.dest_path):
            logger.info(f"检测到移动文件: {event.dest_path}")
            self.callback(event.dest_path, self.folder)


class WatchStrategy(ABC):
    """监控策略抽象基类"""

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        self.folder = folder
        self.on_file_detected = on_file_detected
        self._running = False

    @abstractmethod
    async def start(self) -> None:
        pass

    @abstractmethod
    async def stop(self) -> None:
        pass


class RealtimeStrategy(WatchStrategy):
    """实时监控策略 - 使用 watchdog"""

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._observer: Observer | None = None

    async def start(self) -> None:
        if self._running or not Path(self.folder.path).exists():
            return
        self._observer = Observer()
        handler = VideoFileHandler(self.folder, self.on_file_detected)
        self._observer.schedule(handler, self.folder.path, recursive=True)
        self._observer.start()
        self._running = True
        logger.info(f"[实时模式] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
        self._running = False


class CompatStrategy(WatchStrategy):
    """兼容模式策略 - 定时轮询扫描"""

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._scan_task: asyncio.Task | None = None
        self._known_files: set[str] = set()

    async def start(self) -> None:
        if self._running or not Path(self.folder.path).exists():
            return
        self._running = True
        await self._init_known_files()
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info(f"[兼容模式] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        self._known_files.clear()

    async def _init_known_files(self) -> None:
        for root, _, files in os.walk(self.folder.path):
            for f in files:
                if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                    self._known_files.add(str(Path(root) / f))

    async def _scan_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.folder.scan_interval_seconds)
                if not self._running:
                    break
                current: set[str] = set()
                for root, _, files in os.walk(self.folder.path):
                    for f in files:
                        if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                            current.add(str(Path(root) / f))
                for fp in current - self._known_files:
                    self.on_file_detected(fp, self.folder)
                self._known_files = current
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[兼容模式] 扫描出错: {e}")


class P115ScanStrategy(WatchStrategy):
    """115 网盘监控策略 - 定时轮询 115 目录对比 file_id 找新增文件。

    115 文件已稳定（不像本地文件可能在写入中），检测到新文件后直接加入
    _pending_files（复用稳定等待逻辑统一处理）。
    """

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._scan_task: asyncio.Task | None = None
        self._known_file_ids: set[str] = set()
        self._p115_service: Any = None
        # 新检测文件的元数据（file_id/parent_id），供 _create_jobs_for_files 使用
        self.detected_meta: dict[str, dict] = {}

    async def _get_p115_service(self):
        """懒加载 P115Service（避免 import 循环 + 启动时未登录报错）。"""
        if self._p115_service is None:
            from server.domain.integration.p115_service import P115Service
            from server.domain.system.config_service import ConfigService
            self._p115_service = P115Service(ConfigService())
        return self._p115_service

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # 初始化已知文件集合（首次扫描不触发新增）
        try:
            await self._init_known_files()
        except Exception as e:
            logger.warning(f"[115监控] 初始扫描失败（可能未登录）: {e}")
        self._scan_task = asyncio.create_task(self._scan_loop())
        logger.info(f"[115监控] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        self._known_file_ids.clear()

    async def _init_known_files(self) -> None:
        """首次扫描记录已有文件，不触发回调。"""
        svc = await self._get_p115_service()
        entries = await svc.scan_folder(
            path=self.folder.path,
            file_id=self.folder.file_id,
        )
        for entry in entries:
            fid = entry.get("file_id")
            if fid:
                self._known_file_ids.add(str(fid))

    async def _scan_loop(self) -> None:
        """定时轮询 115 目录，对比 file_id 找新增视频文件。"""
        while self._running:
            try:
                await asyncio.sleep(self.folder.scan_interval_seconds)
                if not self._running:
                    break
                svc = await self._get_p115_service()
                entries = await svc.scan_folder(
                    path=self.folder.path,
                    file_id=self.folder.file_id,
                )
                current_ids: set[str] = set()
                for entry in entries:
                    fid = entry.get("file_id")
                    if not fid:
                        continue
                    fid_str = str(fid)
                    current_ids.add(fid_str)
                    if fid_str not in self._known_file_ids:
                        # 新文件：存元数据并触发回调（加入待处理队列）
                        file_path = entry.get("path", "")
                        if file_path:
                            self.detected_meta[file_path] = {
                                "file_id": fid_str,
                                "parent_id": entry.get("parent_id"),
                            }
                            self.on_file_detected(file_path, self.folder)
                        logger.info(f"[115监控] 检测到新文件: {file_path or fid_str}")
                self._known_file_ids = current_ids
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[115监控] 扫描出错: {e}")


class P115EventStrategy(WatchStrategy):
    """115 网盘事件监控策略 - 定时拉取生活事件 API 找新增视频文件。

    相比 P115ScanStrategy 的全量目录扫描，事件模式只处理增量事件，
    响应更快、开销更低。依赖 115 生活事件 API（life_list）。
    """

    # 表示文件新增类的行为类型（与 115 life_list 实际返回值对齐）
    NEW_FILE_BEHAVIORS = {
        "upload_file", "copy_file", "move_file",
        "receive", "recv", "receive_file",
        "offline_download",
    }

    def __init__(self, folder: WatchedFolder, on_file_detected: Callable[[str, WatchedFolder], None]):
        super().__init__(folder, on_file_detected)
        self._scan_task: asyncio.Task | None = None
        self._last_update_time: int = 0  # 已处理的最大 update_time（去重）
        self._client: Any = None
        self.detected_meta: dict[str, dict] = {}
        # 监控目录及其所有子目录的 id 集合（用于 parent_id 匹配）
        self._watched_dir_ids: set[str] = set()
        # 已处理过的 file_id 集合（防止刮削产生的整理事件导致循环）
        self._processed_file_ids: set[str] = set()

    async def _get_client(self):
        """懒加载 115 client。"""
        if self._client is None:
            from server.domain.integration.p115_service import P115Service
            from server.domain.system.config_service import ConfigService
            svc = P115Service(ConfigService())
            self._client = await svc.load_logged_in_client()
        return self._client

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        # 首次初始化：收集监控目录的子目录 id + 记录当前最大事件时间
        try:
            await self._init()
        except Exception as e:
            logger.warning(f"[115事件] 初始化失败（可能未登录）: {e}")
        self._scan_task = asyncio.create_task(self._event_loop())
        logger.info(f"[115事件] 开始监控: {self.folder.path}")

    async def stop(self) -> None:
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

    async def _init(self) -> None:
        """首次初始化：收集子目录 id + 记录当前最大事件时间（不触发回调）。"""
        # 收集监控目录及其所有子目录的 id（用于 parent_id 匹配）
        await self._collect_dir_ids()

        # 记录当前最大事件时间
        client = await self._get_client()
        resp = await client.life_list(0, async_=True)
        events = self._extract_events(resp)
        if events:
            self._last_update_time = max(e["update_time"] for e in events)

    async def _collect_dir_ids(self) -> None:
        """递归收集监控目录及其所有子目录的 id。"""
        from server.domain.integration.p115_service import P115Service
        from server.domain.system.config_service import ConfigService

        svc = P115Service(ConfigService())
        self._watched_dir_ids.clear()
        if self.folder.file_id:
            self._watched_dir_ids.add(str(self.folder.file_id))
        await self._collect_subdir_ids(svc, self.folder.path, self.folder.file_id)

    async def _collect_subdir_ids(
        self, svc: Any, path: str, file_id: str | None, depth: int = 0
    ) -> None:
        """递归收集子目录 id（限制深度避免过深）。"""
        if depth > 5:
            return
        try:
            result = await svc.browse(path=path, file_id=file_id, page=1, page_size=100)
            for entry in result.get("entries", []):
                if entry.get("is_dir") and entry.get("file_id"):
                    self._watched_dir_ids.add(str(entry["file_id"]))
                    # 递归收集子目录
                    await self._collect_subdir_ids(
                        svc, entry["path"], entry["file_id"], depth + 1
                    )
        except Exception as e:
            logger.warning(f"[115事件] 收集子目录失败: {e}")

    def _extract_events(self, resp: Any) -> list[dict]:
        """从 life_list 响应提取事件列表。"""
        if not isinstance(resp, dict) or not resp.get("state"):
            return []
        data = resp.get("data") or {}
        return data.get("list") or []

    async def _event_loop(self) -> None:
        """定时拉取生活事件，找新增视频文件。"""
        while self._running:
            try:
                await asyncio.sleep(self.folder.scan_interval_seconds)
                if not self._running:
                    break
                client = await self._get_client()
                resp = await client.life_list(0, async_=True)
                events = self._extract_events(resp)
                if not events:
                    continue

                new_max_time = self._last_update_time
                for event in events:
                    evt_time = event.get("update_time", 0)
                    if evt_time <= self._last_update_time:
                        continue  # 已处理
                    new_max_time = max(new_max_time, evt_time)

                    behavior = event.get("behavior_type", "")
                    if behavior not in self.NEW_FILE_BEHAVIORS:
                        continue

                    for item in event.get("items", []):
                        self._process_event_item(item)

                self._last_update_time = new_max_time

                # 定期刷新子目录 id 集合（每 10 轮刷新一次）
                if self._loop_count % 10 == 0:
                    try:
                        await self._collect_dir_ids()
                    except Exception:
                        pass
                self._loop_count += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[115事件] 拉取出错: {e}")

    _loop_count: int = 0

    def _process_event_item(self, item: dict) -> None:
        """处理单个事件项，判断是否为监控目录内的视频新增。"""
        file_id = item.get("file_id")
        file_name = item.get("file_name", "")
        ico = item.get("ico", "")
        parent_id = str(item.get("parent_id", ""))

        # file_id 去重：已处理过的文件不再处理（防止刮削整理事件导致循环）
        if file_id and str(file_id) in self._processed_file_ids:
            return

        # 检查是否视频文件（ico 字段是扩展名，如 "mp4"）
        if f".{ico}" not in VIDEO_EXTENSIONS:
            return

        # 路径匹配：parent_id 在监控目录的子目录 id 集合里才处理
        if self._watched_dir_ids and parent_id not in self._watched_dir_ids:
            return

        # 记录已处理
        if file_id:
            self._processed_file_ids.add(str(file_id))

        # 构造虚拟路径（115 路径）
        # parent_name 是文件所在目录名。如果等于监控目录名（直接子级），
        # 不重复拼接；只有更深的子目录才拼。
        parent_name = item.get("parent_name", "")
        folder_base_name = self.folder.path.rstrip("/").rsplit("/", 1)[-1] if self.folder.path else ""
        if parent_name and parent_name != folder_base_name:
            virtual_path = f"{self.folder.path}/{parent_name}/{file_name}"
        else:
            virtual_path = f"{self.folder.path}/{file_name}"

        self.detected_meta[virtual_path] = {
            "file_id": str(file_id) if file_id else None,
            "parent_id": parent_id,
            "size": item.get("file_size", 0),
        }
        self.on_file_detected(virtual_path, self.folder)
        logger.info(f"[115事件] 检测到新文件: {file_name}")


class WatcherService:
    """Service for folder watching and auto-scraping."""

    def __init__(self, db_path: Path | None = None):
        """Initialize watcher service."""
        self.db_path = db_path or DATABASE_PATH
        self._repo = WatcherRepository(db_path)
        self._status = WatcherStatus.STOPPED
        self._running = False
        self._strategies: dict[str, WatchStrategy] = {}  # folder_id -> strategy
        self._pending_files: dict[str, tuple[str, float, WatchedFolder]] = {}  # path -> (path, detect_time, folder)
        self._last_detection: datetime | None = None
        self._on_files_detected: Callable[[WatcherNotification], None] | None = None
        self._process_task: asyncio.Task | None = None

    async def _ensure_db(self) -> None:
        """确保监控目录表可用并完成旧库列迁移。"""
        await self._repo.ensure_schema()

    async def create_folder(self, folder: WatchedFolderCreate) -> WatchedFolder:
        """Create a new watched folder."""
        await self._ensure_db()

        folder_id = str(uuid.uuid4())[:8]
        now = datetime.now()

        await self._repo.insert_folder(folder_id, folder, now.isoformat())

        new_folder = WatchedFolder(
            id=folder_id,
            path=folder.path,
            enabled=folder.enabled,
            mode=folder.mode,
            scan_interval_seconds=folder.scan_interval_seconds,
            file_stable_seconds=folder.file_stable_seconds,
            auto_scrape=folder.auto_scrape,
            output_dir=folder.output_dir,
            provider=folder.provider,
            file_id=folder.file_id,
            last_scan=None,
            created_at=now,
        )

        # 如果服务正在运行且文件夹启用，立即启动监控
        if self._running and folder.enabled:
            await self._start_folder_watch(new_folder)

        return new_folder

    async def list_folders(self) -> tuple[list[WatchedFolder], int]:
        """List all watched folders."""
        await self._ensure_db()

        rows, total = await self._repo.list_folders()
        folders = [self._row_to_folder(row) for row in rows]
        return folders, total

    async def get_folder(self, folder_id: str) -> WatchedFolder | None:
        """Get a watched folder by ID."""
        await self._ensure_db()

        row = await self._repo.get_folder(folder_id)
        if row is None:
            return None

        return self._row_to_folder(row)

    async def update_folder(
        self, folder_id: str, update: WatchedFolderUpdate
    ) -> WatchedFolder | None:
        """Update a watched folder."""
        await self._ensure_db()

        folder = await self.get_folder(folder_id)
        if folder is None:
            return None

        await self._repo.update_folder(folder_id, update)

        updated_folder = await self.get_folder(folder_id)

        # 如果服务正在运行，重启该文件夹的监控
        if self._running and updated_folder:
            await self._restart_folder_watch(updated_folder)

        return updated_folder

    async def delete_folder(self, folder_id: str) -> bool:
        """Delete a watched folder."""
        await self._ensure_db()

        # 先停止该文件夹的监控
        if folder_id in self._strategies:
            await self._stop_folder_watch(folder_id)

        return await self._repo.delete_folder(folder_id)

    async def get_status(self) -> WatcherStatusResponse:
        """Get watcher status."""
        return WatcherStatusResponse(
            status=self._status,
            active_watchers=len(self._strategies),
            last_detection=self._last_detection,
            pending_files=len(self._pending_files),
        )

    async def _start_folder_watch(self, folder: WatchedFolder) -> None:
        """启动单个文件夹的监控"""
        if folder.id in self._strategies:
            return
        # 根据 provider + mode 选择策略
        if folder.provider == "115":
            if folder.mode == WatcherMode.EVENT:
                strategy: WatchStrategy = P115EventStrategy(folder, self._on_file_detected)
            else:
                strategy = P115ScanStrategy(folder, self._on_file_detected)
        elif folder.mode == WatcherMode.REALTIME:
            strategy = RealtimeStrategy(folder, self._on_file_detected)
        else:
            strategy = CompatStrategy(folder, self._on_file_detected)
        await strategy.start()
        self._strategies[folder.id] = strategy

    async def _stop_folder_watch(self, folder_id: str) -> None:
        """停止单个文件夹的监控"""
        if folder_id in self._strategies:
            await self._strategies[folder_id].stop()
            del self._strategies[folder_id]

    async def _restart_folder_watch(self, folder: WatchedFolder) -> None:
        """重启单个文件夹的监控"""
        await self._stop_folder_watch(folder.id)
        if folder.enabled:
            await self._start_folder_watch(folder)

    async def start(
        self, on_files_detected: Callable[[WatcherNotification], None] | None = None
    ) -> None:
        """Start the watcher service."""
        if self._running:
            return

        self._on_files_detected = on_files_detected
        self._running = True
        self._status = WatcherStatus.RUNNING

        # 获取所有启用的监控文件夹
        folders, _ = await self.list_folders()
        enabled_folders = [f for f in folders if f.enabled]

        if not enabled_folders:
            logger.warning("没有启用的监控文件夹")

        # 为每个文件夹启动独立的监控策略
        for folder in enabled_folders:
            await self._start_folder_watch(folder)

        # 启动待处理文件检查任务
        self._process_task = asyncio.create_task(self._process_pending_files())

        # 在后台执行初始扫描
        asyncio.create_task(self._initial_scan(enabled_folders))

        logger.info(f"监控服务已启动，共 {len(self._strategies)} 个文件夹")

    async def _initial_scan(self, folders: list[WatchedFolder]) -> None:
        """启动时执行一次全量扫描，跳过已有待处理任务的文件"""
        from server.application.scrape_job_service import ScrapeJobService
        scrape_service = ScrapeJobService()
        pending_paths = await scrape_service.get_pending_file_paths()
        logger.info(f"已有 {len(pending_paths)} 个待处理任务，初始扫描将跳过这些文件")

        for folder in folders:
            # 115 网盘目录：用 P115Service 扫描（不走本地 os.walk）
            if folder.provider == "115":
                await self._initial_scan_p115(folder, pending_paths)
                continue

            folder_path = Path(folder.path)
            if not folder_path.exists():
                continue

            logger.info(f"扫描文件夹: {folder.path}")
            stable_files: list[DetectedFile] = []
            current_time = time.time()

            for root, _, files in os.walk(folder_path):
                for filename in files:
                    ext = Path(filename).suffix.lower()
                    if ext not in VIDEO_EXTENSIONS:
                        continue

                    filepath = Path(root) / filename
                    try:
                        stat = filepath.stat()
                        age = current_time - stat.st_mtime

                        if age >= folder.file_stable_seconds:
                            # 跳过已有待处理任务的文件
                            if str(filepath) in pending_paths:
                                continue
                            stable_files.append(
                                DetectedFile(
                                    path=str(filepath),
                                    detected_at=datetime.now(),
                                    file_size=stat.st_size,
                                    stable=True,
                                )
                            )
                        else:
                            self._pending_files[str(filepath)] = (str(filepath), current_time, folder)
                    except OSError:
                        continue

            await self._repo.update_last_scan(folder.id, datetime.now().isoformat())

            if stable_files and folder.auto_scrape:
                logger.info(f"初始扫描发现 {len(stable_files)} 个稳定文件")
                await self._create_jobs_for_files(stable_files, folder)

    async def _initial_scan_p115(self, folder: WatchedFolder, pending_paths: set[str]) -> None:
        """启动时扫描 115 目录（不走本地 os.walk）。"""
        try:
            from server.domain.integration.p115_service import P115Service
            from server.domain.system.config_service import ConfigService
            svc = P115Service(ConfigService())
            entries = await svc.scan_folder(path=folder.path, file_id=folder.file_id)
        except Exception as e:
            logger.warning(f"[115监控] 初始扫描失败（可能未登录）: {e}")
            return

        stable_files: list[DetectedFile] = []
        for entry in entries:
            file_path = entry.get("path", "")
            if file_path in pending_paths:
                continue
            stable_files.append(
                DetectedFile(
                    path=file_path,
                    detected_at=datetime.now(),
                    file_size=entry.get("size") or 0,
                    stable=True,
                    file_id=entry.get("file_id"),
                    parent_id=entry.get("parent_id"),
                )
            )

        await self._repo.update_last_scan(folder.id, datetime.now().isoformat())

        if stable_files and folder.auto_scrape:
            logger.info(f"[115监控] 初始扫描发现 {len(stable_files)} 个文件")
            await self._create_jobs_for_files(stable_files, folder)

    def _on_file_detected(self, path: str, folder: WatchedFolder) -> None:
        """文件检测回调"""
        self._last_detection = datetime.now()
        self._pending_files[path] = (path, time.time(), folder)
        logger.info(f"文件加入待处理队列: {path}")

    def _get_p115_meta(self, folder_id: str, file_path: str) -> dict | None:
        """从 P115 策略的 detected_meta 获取文件的 file_id/parent_id。"""
        strategy = self._strategies.get(folder_id)
        if strategy and isinstance(strategy, (P115ScanStrategy, P115EventStrategy)):
            return strategy.detected_meta.pop(file_path, None)
        return None

    async def _process_pending_files(self) -> None:
        """处理待处理文件的后台任务"""
        while self._running:
            try:
                await asyncio.sleep(5)

                if not self._pending_files:
                    continue

                current_time = time.time()
                # 按 folder 分组收集稳定文件
                stable_by_folder: dict[str, tuple[list[DetectedFile], WatchedFolder]] = {}
                to_remove: list[str] = []

                for path, (file_path, detect_time, folder) in list(self._pending_files.items()):
                    age = current_time - detect_time

                    if age >= folder.file_stable_seconds:
                        if folder.provider == "115":
                            # 115 文件不做本地 stat 检查（已稳定），直接加入
                            key = folder.id
                            if key not in stable_by_folder:
                                stable_by_folder[key] = ([], folder)
                            # 从 P115ScanStrategy 的 detected_meta 取 file_id
                            meta = self._get_p115_meta(folder.id, file_path)
                            stable_by_folder[key][0].append(
                                DetectedFile(
                                    path=file_path,
                                    detected_at=datetime.now(),
                                    file_size=meta.get("size", 0) if meta else 0,
                                    stable=True,
                                    file_id=meta.get("file_id") if meta else None,
                                    parent_id=meta.get("parent_id") if meta else None,
                                )
                            )
                            to_remove.append(path)
                        else:
                            try:
                                stat = Path(file_path).stat()
                                file_age = current_time - stat.st_mtime

                                if file_age >= folder.file_stable_seconds:
                                    key = folder.id
                                    if key not in stable_by_folder:
                                        stable_by_folder[key] = ([], folder)
                                    stable_by_folder[key][0].append(
                                        DetectedFile(
                                            path=file_path,
                                            detected_at=datetime.now(),
                                            file_size=stat.st_size,
                                            stable=True,
                                        )
                                    )
                                    to_remove.append(path)
                            except OSError:
                                to_remove.append(path)

                for path in to_remove:
                    self._pending_files.pop(path, None)

                # 按 folder 分组创建任务（各自用独立的 output_dir）
                for files, folder in stable_by_folder.values():
                    if folder.auto_scrape:
                        logger.info(f"处理 {len(files)} 个稳定文件 (folder={folder.path})")
                        await self._create_jobs_for_files(files, folder)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"处理待处理文件时出错: {e}")

    async def stop(self) -> None:
        """Stop the watcher service."""
        self._running = False
        self._status = WatcherStatus.STOPPED

        # 停止所有文件夹的监控
        for folder_id in list(self._strategies.keys()):
            await self._stop_folder_watch(folder_id)

        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None

        logger.info("监控服务已停止")

    async def _create_jobs_for_files(
        self, files: list[DetectedFile], folder: WatchedFolder | None = None
    ) -> None:
        """为检测到的文件创建刮削任务。

        folder.output_dir 优先于全局整理目录配置。
        """
        from server.application.scrape_job_service import ScrapeJobService
        from server.domain.system.config_service import ConfigService
        from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource

        config_service = ConfigService()
        organize_config = await config_service.get_organize_config()

        # 优先用 folder 独立配置，回退全局配置
        organize_dir = folder.output_dir if folder and folder.output_dir else organize_config.organize_dir
        metadata_dir = organize_config.metadata_dir
        link_mode = organize_config.organize_mode  # 读取整理模式配置

        if not organize_dir:
            logger.warning("未配置整理目录，跳过创建任务")
            return

        scrape_service = ScrapeJobService()

        # 对 115 folder 预解析 output_locator（整理目标目录的 115 file_id）
        from server.models.storage import StorageLocator, StorageProvider
        output_locator = None
        if folder and folder.provider == "115" and organize_dir and organize_dir.startswith("/115网盘"):
            try:
                from server.domain.integration.p115_service import P115Service
                p115_svc = P115Service(config_service)
                out_dir_id = await p115_svc.resolve_directory_id_by_path(organize_dir)
                output_locator = StorageLocator(
                    provider=StorageProvider.P115,
                    path=organize_dir,
                    file_id=out_dir_id,
                    is_dir=True,
                )
            except Exception as e:
                logger.warning(f"[115监控] 解析输出目录失败: {e}")

        for file in files:
            logger.info(f"为文件创建刮削任务: {file.path}")

            # 115 源文件构造 file_locator（携带 file_id 以便刮削下载/在线处理）
            file_locator = None
            if folder and folder.provider == "115" and file.file_id:
                file_locator = StorageLocator(
                    provider=StorageProvider.P115,
                    path=file.path,
                    file_id=file.file_id,
                    parent_id=file.parent_id or folder.file_id,
                    is_dir=False,
                )

            job_create = ScrapeJobCreate(
                file_path=file.path,
                output_dir=organize_dir,
                metadata_dir=metadata_dir,
                file_locator=file_locator,
                output_locator=output_locator,
                link_mode=link_mode,  # 传递整理模式
                source=ScrapeJobSource.WATCHER,
            )
            await scrape_service.create_job(job_create)

    def _row_to_folder(self, row) -> WatchedFolder:
        """Convert database row to WatchedFolder."""
        mode_value = row["mode"] if "mode" in row.keys() else "realtime"
        output_dir = row["output_dir"] if "output_dir" in row.keys() else None
        provider = row["provider"] if "provider" in row.keys() else "local"
        file_id = row["file_id"] if "file_id" in row.keys() else None
        return WatchedFolder(
            id=row["id"],
            path=row["path"],
            enabled=bool(row["enabled"]),
            mode=WatcherMode(mode_value),
            scan_interval_seconds=row["scan_interval_seconds"],
            file_stable_seconds=row["file_stable_seconds"],
            auto_scrape=bool(row["auto_scrape"]),
            output_dir=output_dir,
            provider=provider,
            file_id=file_id,
            last_scan=datetime.fromisoformat(row["last_scan"]) if row["last_scan"] else None,
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        )
