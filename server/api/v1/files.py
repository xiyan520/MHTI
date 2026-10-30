"""File scanning API routes.

异常处理：所有 FileSystemError 子类（FolderNotFoundError、InvalidFolderError、
PermissionDeniedError）由全局异常处理器统一处理，无需在 API 层手动捕获。
"""

from fastapi import APIRouter, Depends, Query

from server.api.deps import get_file_service, get_history_service, get_p115_service, require_auth
from server.application.history_service import HistoryService
from server.application.library import FolderScanUseCase
from server.domain.integration.p115_service import P115Service
from server.domain.media.file_service import FileService
from server.models.file import BrowseResponse, ScanRequest, ScanResponse
from server.models.storage import StorageProvider

router = APIRouter(prefix="/api", tags=["files"], dependencies=[Depends(require_auth)])


def get_folder_scan_use_case(
    file_service: FileService = Depends(get_file_service),
    history_service: HistoryService = Depends(get_history_service),
) -> FolderScanUseCase:
    """构造目录扫描用例（依赖经 FastAPI 覆盖链注入）。"""
    return FolderScanUseCase(file_service, history_service)


@router.post("/scan", response_model=ScanResponse)
async def scan_folder(
    request: ScanRequest,
    use_case: FolderScanUseCase = Depends(get_folder_scan_use_case),
) -> ScanResponse:
    """
    Scan a folder for video files.

    Args:
        request: ScanRequest containing the folder path.

    Returns:
        ScanResponse with list of discovered video files.

    Raises:
        FolderNotFoundError: 文件夹不存在 (404)
        InvalidFolderError: 无效文件夹路径 (400)
        PermissionDeniedError: 权限被拒绝 (403)
    """
    folder_path, files = await use_case.scan(request.folder_path, request.locator)
    return ScanResponse(
        folder_path=folder_path,
        total_files=len(files),
        files=files,
    )


@router.get("/files/browse", response_model=BrowseResponse)
async def browse_directory(
    path: str = Query(default="", description="Directory path to browse"),
    provider: StorageProvider = Query(
        default=StorageProvider.LOCAL,
        description="Storage provider to browse",
    ),
    file_id: str | None = Query(default=None, description="Provider file id"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=20, ge=1, le=100, description="Items per page"),
    file_service: FileService = Depends(get_file_service),
    p115_service: P115Service = Depends(get_p115_service),
) -> BrowseResponse:
    """
    Browse a directory and list its contents.

    Args:
        path: Path to browse. Empty for root/drives.
        page: Page number (1-based).
        page_size: Number of items per page.
        file_service: Injected FileService instance.
        p115_service: 用于判断 115 登录态，决定根目录是否展示 115 虚入口。

    Returns:
        BrowseResponse with directory entries.

    Raises:
        FolderNotFoundError: 文件夹不存在 (404)
        InvalidFolderError: 无效文件夹路径 (400)
        PermissionDeniedError: 权限被拒绝 (403)
    """
    # 未登录 115（或已清除登录）时不注入虚入口：它点进去只会报“请先登录 115 网盘”，
    # 对用户没有可操作性。登录态取自配置库，登录后下一次浏览即恢复入口。
    cloud_status = await p115_service.get_status()
    include_cloud_mounts = cloud_status.is_logged_in

    if provider == StorageProvider.P115:
        (
            current_path,
            parent_path,
            entries,
            total,
            current_file_id,
            parent_file_id,
        ) = await file_service.browse_directory_async(
            path=path,
            provider=provider,
            file_id=file_id,
            page=page,
            page_size=page_size,
        )
    else:
        (
            current_path,
            parent_path,
            entries,
            total,
            current_file_id,
            parent_file_id,
        ) = file_service.browse_directory(
            path=path,
            provider=provider,
            file_id=file_id,
            page=page,
            page_size=page_size,
            include_cloud_mounts=include_cloud_mounts,
        )
    return BrowseResponse(
        current_path=current_path,
        parent_path=parent_path,
        entries=entries,
        total=total,
        page=page,
        page_size=page_size,
        current_file_id=current_file_id,
        parent_file_id=parent_file_id,
    )
