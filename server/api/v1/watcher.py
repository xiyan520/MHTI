"""Watcher API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from server.api.deps import get_watcher_service, require_auth
from server.application.watcher_service import WatcherService
from server.models.watcher import (
    WatchedFolder,
    WatchedFolderCreate,
    WatchedFolderListResponse,
    WatchedFolderUpdate,
    WatcherStatusResponse,
)

router = APIRouter(prefix="/api/watcher", tags=["watcher"], dependencies=[Depends(require_auth)])


@router.get("/status", response_model=WatcherStatusResponse)
async def get_status(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatcherStatusResponse:
    """Get watcher service status."""
    return await watcher_service.get_status()


@router.post("/start")
async def start_watcher(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> dict:
    """Start the watcher service."""
    await watcher_service.start()
    return {"success": True, "message": "监控服务已启动"}


@router.post("/stop")
async def stop_watcher(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> dict:
    """Stop the watcher service."""
    await watcher_service.stop()
    return {"success": True, "message": "监控服务已停止"}


@router.get("/folders", response_model=WatchedFolderListResponse)
async def list_folders(
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolderListResponse:
    """List all watched folders."""
    folders, total = await watcher_service.list_folders()
    return WatchedFolderListResponse(folders=folders, total=total)


@router.post("/folders", response_model=WatchedFolder)
async def create_folder(
    folder: WatchedFolderCreate,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolder:
    """Create a new watched folder."""
    return await watcher_service.create_folder(folder)


@router.get("/folders/{folder_id}", response_model=WatchedFolder)
async def get_folder(
    folder_id: str,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolder:
    """Get a watched folder by ID."""
    folder = await watcher_service.get_folder(folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


@router.put("/folders/{folder_id}", response_model=WatchedFolder)
async def update_folder(
    folder_id: str,
    update: WatchedFolderUpdate,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> WatchedFolder:
    """Update a watched folder."""
    folder = await watcher_service.update_folder(folder_id, update)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    return folder


@router.delete("/folders/{folder_id}")
async def delete_folder(
    folder_id: str,
    watcher_service: WatcherService = Depends(get_watcher_service),
) -> dict:
    """Delete a watched folder."""
    deleted = await watcher_service.delete_folder(folder_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Folder not found")
    return {"success": True, "message": "监控文件夹已删除"}
