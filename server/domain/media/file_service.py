"""File scanning service for video file discovery."""

import asyncio
from pathlib import Path
from typing import Any

from server.common.exceptions import (
    FolderNotFoundError,
    InvalidFolderError,
    PermissionDeniedError,
)
from server.models.file import DirectoryEntry, ScannedFile
from server.models.storage import StorageLocator, StorageProvider

# Supported video file extensions
SUPPORTED_VIDEO_EXTENSIONS: set[str] = {
    ".mp4",
    ".mkv",
    ".avi",
    ".wmv",
    ".mov",
    ".flv",
    ".rmvb",
    ".ts",
    ".m2ts",
    ".bdmv",
    ".webm",
    ".3gp",
    ".mpg",
    ".mpeg",
    ".vob",
    ".iso",
}

# 禁止访问的系统目录（安全防护）
BLOCKED_PATHS = {
    "/etc", "/var", "/usr", "/bin", "/sbin", "/boot", "/root", "/proc", "/sys",
    r"C:\Windows", r"C:\Program Files", r"C:\Program Files (x86)",
}
VIRTUAL_115_ROOT_NAME = "115网盘"
VIRTUAL_115_ROOT_PATH = f"/{VIRTUAL_115_ROOT_NAME}"
VIRTUAL_115_ROOT_ID = "0"


def _sanitize_path(path_str: str) -> Path:
    """
    Sanitize and validate path to prevent path traversal attacks.

    Args:
        path_str: Raw path string from user input.

    Returns:
        Sanitized Path object.

    Raises:
        InvalidFolderError: If path contains dangerous patterns.
    """
    if not path_str:
        return Path("")

    # 检查危险模式
    dangerous_patterns = ["..", "~", "\x00"]
    for pattern in dangerous_patterns:
        if pattern in path_str:
            raise InvalidFolderError(f"路径包含非法字符: {pattern}")

    # 规范化路径
    path = Path(path_str).resolve()

    # 检查是否在禁止目录中
    path_str_normalized = str(path).replace("\\", "/")
    for blocked in BLOCKED_PATHS:
        blocked_normalized = blocked.replace("\\", "/")
        if path_str_normalized.startswith(blocked_normalized):
            raise PermissionDeniedError(f"禁止访问系统目录: {blocked}")

    return path


class FileService:
    """Service for scanning folders and discovering video files."""

    def _get_115_service(self):
        """Return the current 115 service for provider-aware browsing."""
        from server.domain.integration.p115_service import P115Service
        from server.domain.system.config_service import ConfigService

        return P115Service(ConfigService())

    def scan_folder(
        self,
        folder_path: str,
        locator: StorageLocator | None = None,
    ) -> list[ScannedFile]:
        """
        Scan a folder recursively for video files.

        Args:
            folder_path: Path to the folder to scan.
            locator: Optional storage locator. When provider is P115 the scan
                is delegated to the 115 cloud service.

        Returns:
            List of ScannedFile objects representing discovered video files.

        Raises:
            FolderNotFoundError: If the folder does not exist.
            InvalidFolderError: If the path is not a directory.
            PermissionDeniedError: If access to the folder is denied.
        """
        if locator is not None and locator.provider == StorageProvider.P115:
            return self._scan_provider_p115(folder_path, locator)

        # 路径安全验证
        path = _sanitize_path(folder_path)

        # Validate folder exists
        if not path.exists():
            raise FolderNotFoundError(folder_path)

        # Validate path is a directory
        if not path.is_dir():
            raise InvalidFolderError(folder_path)

        # Scan for video files
        try:
            return self._scan_recursive(path)
        except PermissionError as e:
            raise PermissionDeniedError(folder_path) from e

    def _scan_provider_p115(
        self,
        folder_path: str,
        locator: StorageLocator,
    ) -> list[ScannedFile]:
        """Scan a 115 cloud directory via the async provider service (sync wrapper)."""
        import asyncio

        service = self._get_115_service()
        scan_method = getattr(service, "scan_folder", None)
        if scan_method is None:
            raise InvalidFolderError("115 provider 扫描不可用")

        coro = scan_method(
            path=locator.path or folder_path,
            file_id=locator.file_id,
        )
        # Sync path only valid when no loop is running; otherwise use scan_folder_async.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            entries = asyncio.run(coro)
        else:
            raise InvalidFolderError(
                folder_path,
                reason="115 provider 扫描仅支持同步调用，请在事件循环外调用或使用 scan_folder_async",
            )
        return [self._p115_entry_to_scanned_file(entry) for entry in entries]

    async def scan_folder_async(
        self,
        folder_path: str,
        locator: StorageLocator | None = None,
    ) -> list[ScannedFile]:
        """Async variant of :meth:`scan_folder` for provider-backed sources."""
        if locator is not None and locator.provider == StorageProvider.P115:
            service = self._get_115_service()
            scan_method = getattr(service, "scan_folder", None)
            if scan_method is None:
                raise InvalidFolderError("115 provider 扫描不可用")
            entries = await scan_method(
                path=locator.path or folder_path,
                file_id=locator.file_id,
            )
            return [self._p115_entry_to_scanned_file(entry) for entry in entries]
        return self.scan_folder(folder_path, locator=locator)

    @staticmethod
    def _p115_entry_to_scanned_file(entry: dict[str, Any]) -> ScannedFile:
        """Convert a 115 scan entry dict into a ScannedFile model."""
        name = entry.get("name") or ""
        suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        return ScannedFile(
            filename=name,
            path=entry.get("path") or name,
            size=int(entry["size"]) if entry.get("size") is not None else 0,
            extension=suffix,
            mtime=entry.get("mtime"),
            file_id=entry.get("file_id"),
            parent_id=entry.get("parent_id"),
        )

    def _scan_recursive(self, folder: Path) -> list[ScannedFile]:
        """
        Recursively scan a folder for video files.

        Args:
            folder: Path object of the folder to scan.

        Returns:
            List of ScannedFile objects.
        """
        from datetime import datetime

        video_files: list[ScannedFile] = []

        for item in folder.rglob("*"):
            if item.is_file() and item.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS:
                stat = item.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
                video_files.append(
                    ScannedFile(
                        filename=item.name,
                        path=str(item.absolute()),
                        size=stat.st_size,
                        extension=item.suffix.lower(),
                        mtime=mtime,
                    )
                )

        return video_files

    def _build_virtual_115_entry(self) -> DirectoryEntry:
        """Create the virtual root entry for 115 cloud storage."""
        return DirectoryEntry(
            name=VIRTUAL_115_ROOT_NAME,
            path=VIRTUAL_115_ROOT_PATH,
            is_dir=True,
            provider=StorageProvider.P115,
            file_id=VIRTUAL_115_ROOT_ID,
            is_virtual=True,
        )

    def _build_root_entries(self, include_cloud_mounts: bool = True) -> list[DirectoryEntry]:
        """Build root entries for local storage plus the virtual 115 entry.

        Args:
            include_cloud_mounts: 是否注入 115 网盘虚入口。未登录 115 时调用方
                传 False——该入口点进去只会报“请先登录”，没有可操作性。
        """
        import platform

        entries = [self._build_virtual_115_entry()] if include_cloud_mounts else []
        if platform.system() != "Windows":
            return entries

        import string

        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if Path(drive).exists():
                entries.append(
                    DirectoryEntry(
                        name=f"{letter}:",
                        path=drive,
                        is_dir=True,
                    )
                )
        return entries

    def _coerce_directory_entry(
        self,
        row: DirectoryEntry | dict[str, Any],
        provider: StorageProvider,
    ) -> DirectoryEntry:
        """Normalize provider rows into DirectoryEntry models."""
        if isinstance(row, DirectoryEntry):
            return row

        data = dict(row)
        data.setdefault("provider", provider)
        data.setdefault("file_id", None)
        data.setdefault("parent_id", None)
        data.setdefault("is_virtual", False)
        return DirectoryEntry(**data)

    def _coerce_provider_browse_result(
        self,
        result: tuple[str, str | None, list[DirectoryEntry], int] | dict[str, Any],
        fallback_path: str,
    ) -> tuple[str, str | None, list[DirectoryEntry], int, str | None, str | None]:
        """Normalize provider browse results into a shared tuple structure.

        Returns ``(current_path, parent_path, entries, total, current_file_id,
        parent_file_id)``. The file ids are None for local storage and only
        populated by provider backends (e.g. 115) so callers can navigate
        back to the parent directory.
        """
        if isinstance(result, tuple):
            current_path, parent_path, rows, total = result
            current_file_id = None
            parent_file_id = None
        else:
            current_path = result.get("current_path", fallback_path or VIRTUAL_115_ROOT_PATH)
            parent_path = result.get("parent_path")
            rows = result.get("entries", [])
            total = result.get("total", len(rows))
            current_file_id = result.get("current_file_id")
            parent_file_id = result.get("parent_file_id")

        entries = [
            self._coerce_directory_entry(row, StorageProvider.P115)
            for row in rows
        ]
        return current_path, parent_path, entries, total, current_file_id, parent_file_id

    def _browse_provider_115(
        self,
        path: str,
        file_id: str | None,
        page: int,
        page_size: int,
    ) -> tuple[str, str | None, list[DirectoryEntry], int]:
        """Delegate sync browse requests to the async 115 provider service."""
        service = self._get_115_service()
        browse_method = getattr(service, "browse", None)
        if browse_method is None:
            raise InvalidFolderError("115 provider browsing is not available")

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(
                browse_method(
                    path=path or VIRTUAL_115_ROOT_PATH,
                    file_id=file_id,
                    page=page,
                    page_size=page_size,
                )
            )
            return self._coerce_provider_browse_result(result, path)

        raise InvalidFolderError(
            path or VIRTUAL_115_ROOT_PATH,
            reason="115 provider 浏览仅支持异步调用",
        )

    async def _browse_provider_115_async(
        self,
        path: str,
        file_id: str | None,
        page: int,
        page_size: int,
    ) -> tuple[str, str | None, list[DirectoryEntry], int]:
        """Delegate async browse requests to the 115 provider service."""
        service = self._get_115_service()
        browse_method = getattr(service, "browse", None)
        if browse_method is None:
            raise InvalidFolderError("115 provider browsing is not available")

        result = await browse_method(
            path=path or VIRTUAL_115_ROOT_PATH,
            file_id=file_id,
            page=page,
            page_size=page_size,
        )
        return self._coerce_provider_browse_result(result, path)

    def _browse_local_root(
        self,
        page: int,
        page_size: int,
        include_cloud_mounts: bool = True,
    ) -> tuple[str, None, list[DirectoryEntry], int, None, None]:
        """Browse the synthetic root entry list."""
        entries = self._build_root_entries(include_cloud_mounts=include_cloud_mounts)
        total = len(entries)
        start = (page - 1) * page_size
        end = start + page_size
        return "", None, entries[start:end], total, None, None

    def _browse_local_path(
        self,
        path: str,
        page: int,
        page_size: int,
        include_cloud_mounts: bool = True,
    ) -> tuple[str, str | None, list[DirectoryEntry], int, None, None]:
        """Browse a local filesystem directory."""
        import platform
        from datetime import datetime

        folder = _sanitize_path(path)

        if not folder.exists():
            raise FolderNotFoundError(path)

        if not folder.is_dir():
            raise InvalidFolderError(path)

        try:
            all_entries: list[DirectoryEntry] = []

            if str(folder.absolute()) == "/" and include_cloud_mounts:
                all_entries.append(self._build_virtual_115_entry())

            for item in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    stat = item.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
                    size = stat.st_size if item.is_file() else None

                    all_entries.append(
                        DirectoryEntry(
                            name=item.name,
                            path=str(item.absolute()),
                            is_dir=item.is_dir(),
                            size=size,
                            mtime=mtime,
                        )
                    )
                except PermissionError:
                    continue

            total = len(all_entries)
            start = (page - 1) * page_size
            end = start + page_size
            entries = all_entries[start:end]

            parent = folder.parent
            parent_path = str(parent.absolute()) if parent != folder else None

            if platform.system() == "Windows" and parent_path and len(parent_path) == 3:
                pass
            elif platform.system() == "Windows" and str(folder.absolute()).endswith(":\\"):
                parent_path = ""

            return str(folder.absolute()), parent_path, entries, total, None, None

        except PermissionError as e:
            raise PermissionDeniedError(path) from e

    def _normalize_provider(self, provider: str | StorageProvider) -> str:
        """Normalize provider enum/string input to a string value."""
        if isinstance(provider, StorageProvider):
            return provider.value
        return provider or StorageProvider.LOCAL.value

    def browse_directory(
        self,
        path: str = "",
        page: int = 1,
        page_size: int = 20,
        provider: str | StorageProvider = StorageProvider.LOCAL,
        file_id: str | None = None,
        include_cloud_mounts: bool = True,
    ) -> tuple[str, str | None, list[DirectoryEntry], int, str | None, str | None]:
        """
        Browse a directory and list its contents.

        Args:
            path: Path to browse. Empty string returns root/drives.
            page: Page number (1-based).
            page_size: Number of items per page.
            include_cloud_mounts: 本地根目录是否注入 115 网盘虚入口（未登录时传 False）。

        Returns:
            Tuple of (current_path, parent_path, entries, total,
            current_file_id, parent_file_id). The file ids are None for local
            storage and populated by provider backends (e.g. 115) so callers
            can navigate back to the parent directory.

        Raises:
            FolderNotFoundError: If the path does not exist.
            InvalidFolderError: If the path is not a directory.
            PermissionDeniedError: If access is denied.
        """
        import platform
        provider_value = self._normalize_provider(provider)

        if provider_value == StorageProvider.P115.value:
            return self._browse_provider_115(path, file_id, page, page_size)

        if not path:
            if platform.system() == "Windows":
                return self._browse_local_root(
                    page, page_size, include_cloud_mounts=include_cloud_mounts
                )
            path = "/"

        return self._browse_local_path(
            path, page, page_size, include_cloud_mounts=include_cloud_mounts
        )

    async def browse_directory_async(
        self,
        path: str = "",
        page: int = 1,
        page_size: int = 20,
        provider: str | StorageProvider = StorageProvider.LOCAL,
        file_id: str | None = None,
        include_cloud_mounts: bool = True,
    ) -> tuple[str, str | None, list[DirectoryEntry], int, str | None, str | None]:
        """Browse provider-aware directories without blocking the event loop."""
        provider_value = self._normalize_provider(provider)

        if provider_value == StorageProvider.P115.value:
            return await self._browse_provider_115_async(path, file_id, page, page_size)

        return self.browse_directory(
            path=path,
            page=page,
            page_size=page_size,
            provider=provider,
            file_id=file_id,
            include_cloud_mounts=include_cloud_mounts,
        )
