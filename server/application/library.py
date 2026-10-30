"""媒体库扫描用例 - 扫描目录并过滤已刮削文件。

从 api/files.py 下沉，路由只做协议转换。
"""

from server.application.history_service import HistoryService
from server.common.exceptions import validation_error
from server.domain.media.file_service import FileService
from server.domain.media.fingerprint_service import calculate_fingerprint
from server.models.file import ScannedFile
from server.models.storage import StorageLocator, StorageProvider


class FolderScanUseCase:
    """扫描文件夹并按指纹过滤已刮削文件。"""

    def __init__(
        self,
        file_service: FileService,
        history_service: HistoryService,
    ) -> None:
        self._file_service = file_service
        self._history_service = history_service

    async def scan(
        self,
        folder_path: str,
        locator: StorageLocator | None,
    ) -> tuple[str, list[ScannedFile]]:
        """执行扫描。

        Returns:
            (显示用目录路径, 待刮削的文件列表)
        """
        is_p115 = bool(locator and locator.provider == StorageProvider.P115)

        if not folder_path.strip() and not is_p115:
            raise validation_error("folder_path 不能为空", field="folder_path")

        if is_p115:
            files = await self._file_service.scan_folder_async(
                locator.path or folder_path,
                locator=locator,
            )
            # 115 文件没有本地指纹，直接返回
            return locator.path or folder_path, files

        files = self._file_service.scan_folder(folder_path)

        # 计算文件指纹并过滤已刮削的文件
        fingerprint_map = {}  # path -> fingerprint
        for f in files:
            fp = calculate_fingerprint(f.path)
            if fp:
                fingerprint_map[f.path] = fp

        # 查询已存在的指纹
        existing_fps = await self._history_service.get_existing_fingerprints(
            list(fingerprint_map.values())
        )

        # 过滤掉已刮削的文件
        filtered_files = [
            f for f in files
            if fingerprint_map.get(f.path) not in existing_fps
        ]
        return folder_path, filtered_files
