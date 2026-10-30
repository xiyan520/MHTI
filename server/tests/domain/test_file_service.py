"""Unit tests for FileService."""

import pytest

from server.common.exceptions import FolderNotFoundError, InvalidFolderError
from server.domain.media.file_service import SUPPORTED_VIDEO_EXTENSIONS, FileService
from server.models.file import DirectoryEntry, ScanRequest
from server.models.storage import StorageLocator, StorageProvider


class TestFileService:
    """Tests for FileService class."""

    def test_scan_folder_finds_video_files(self, tmp_path, file_service):
        """Test that video files are correctly identified."""
        # Create test files
        (tmp_path / "video.mp4").touch()
        (tmp_path / "video.mkv").touch()
        (tmp_path / "document.pdf").touch()
        (tmp_path / "image.jpg").touch()

        result = file_service.scan_folder(str(tmp_path))

        assert len(result) == 2
        extensions = {f.extension for f in result}
        assert extensions == {".mp4", ".mkv"}

    def test_scan_folder_recursive(self, tmp_path, file_service):
        """Test that subdirectories are scanned recursively."""
        # Create nested directory structure
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        nested = subdir / "nested"
        nested.mkdir()

        (tmp_path / "root.mp4").touch()
        (subdir / "sub.mkv").touch()
        (nested / "deep.avi").touch()

        result = file_service.scan_folder(str(tmp_path))

        assert len(result) == 3
        filenames = {f.filename for f in result}
        assert filenames == {"root.mp4", "sub.mkv", "deep.avi"}

    def test_scan_folder_empty_directory(self, tmp_path, file_service):
        """Test scanning an empty directory returns empty list."""
        result = file_service.scan_folder(str(tmp_path))

        assert result == []

    def test_scan_folder_no_video_files(self, tmp_path, file_service):
        """Test scanning a directory with no video files."""
        (tmp_path / "document.pdf").touch()
        (tmp_path / "image.png").touch()
        (tmp_path / "text.txt").touch()

        result = file_service.scan_folder(str(tmp_path))

        assert result == []

    def test_scan_folder_not_found(self, file_service):
        """Test that FolderNotFoundError is raised for non-existent path."""
        with pytest.raises(FolderNotFoundError) as exc_info:
            file_service.scan_folder("/nonexistent/path/to/folder")

        assert "/nonexistent/path/to/folder" in str(exc_info.value)

    def test_scan_folder_invalid_folder(self, tmp_path, file_service):
        """Test that InvalidFolderError is raised when path is a file."""
        file_path = tmp_path / "not_a_folder.txt"
        file_path.touch()

        with pytest.raises(InvalidFolderError) as exc_info:
            file_service.scan_folder(str(file_path))

        assert "not_a_folder.txt" in str(exc_info.value)

    def test_scanned_file_has_correct_attributes(self, tmp_path, file_service):
        """Test that ScannedFile has all required attributes."""
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"fake video content")

        result = file_service.scan_folder(str(tmp_path))

        assert len(result) == 1
        scanned = result[0]
        assert scanned.filename == "test_video.mp4"
        assert scanned.path.endswith("test_video.mp4")
        assert scanned.size == 18  # len(b"fake video content")
        assert scanned.extension == ".mp4"

    def test_scan_folder_case_insensitive_extensions(self, tmp_path, file_service):
        """Test that file extensions are matched case-insensitively."""
        (tmp_path / "video.MP4").touch()
        (tmp_path / "video.MKV").touch()
        (tmp_path / "video.Avi").touch()

        result = file_service.scan_folder(str(tmp_path))

        assert len(result) == 3

    def test_supported_extensions_constant(self):
        """Test that all expected extensions are supported."""
        expected = {
            ".mp4", ".mkv", ".avi", ".wmv", ".mov", ".flv",
            ".rmvb", ".ts", ".m2ts", ".bdmv", ".webm",
            ".3gp", ".mpg", ".mpeg", ".vob", ".iso",
        }
        assert SUPPORTED_VIDEO_EXTENSIONS == expected


class TestProviderAwareModels:
    """Tests for provider-aware file models."""

    def test_directory_entry_supports_provider_metadata(self):
        """DirectoryEntry should expose provider-specific metadata fields."""
        entry = DirectoryEntry(
            name="115网盘",
            path="/115网盘",
            is_dir=True,
            provider="115",
            file_id="0",
            parent_id=None,
            is_virtual=True,
        )

        assert entry.provider == "115"
        assert entry.file_id == "0"
        assert entry.parent_id is None
        assert entry.is_virtual is True

    def test_scan_request_supports_locator(self):
        """ScanRequest should accept a provider-aware locator payload."""
        request = ScanRequest(
            locator=StorageLocator(
                provider=StorageProvider.P115,
                path="/115网盘",
                file_id="0",
            ),
        )

        assert request.folder_path == ""
        assert request.locator is not None
        assert request.locator.provider == StorageProvider.P115
        assert request.locator.file_id == "0"


class TestProviderAwareBrowse:
    """Tests for provider-aware directory browsing."""

    def test_browse_directory_root_contains_115_virtual_entry(self, file_service: FileService):
        """Root browse should inject the virtual 115 cloud entry."""
        current_path, parent_path, entries, total, _cfid, _pfid = file_service.browse_directory(
            "", page=1, page_size=100
        )

        virtual_entry = next(
            entry for entry in entries if entry.name == "115网盘"
        )

        assert current_path in {"", "/"}
        assert parent_path is None
        assert virtual_entry.path == "/115网盘"
        assert virtual_entry.provider == "115"
        assert virtual_entry.file_id == "0"
        assert virtual_entry.is_virtual is True
        assert total >= 1

    def test_browse_directory_root_can_omit_115_virtual_entry(self, file_service: FileService):
        """include_cloud_mounts=False 时根目录不注入 115 虚入口。"""
        current_path, parent_path, entries, total, _cfid, _pfid = file_service.browse_directory(
            "", page=1, page_size=100, include_cloud_mounts=False
        )

        assert current_path in {"", "/"}
        assert parent_path is None
        assert all(entry.name != "115网盘" for entry in entries)
        assert total == len(entries)

    @pytest.mark.asyncio
    async def test_browse_directory_async_provider_115_uses_provider_service(
        self,
        file_service: FileService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """115 provider browse should delegate through the async provider path."""

        class Fake115Service:
            def __init__(self):
                self.calls = []

            async def browse(
                self,
                *,
                path: str,
                file_id: str | None,
                page: int,
                page_size: int,
            ):
                self.calls.append(
                    {
                        "path": path,
                        "file_id": file_id,
                        "page": page,
                        "page_size": page_size,
                    }
                )
                return {
                    "current_path": "/115网盘/电影",
                    "parent_path": "/115网盘",
                    "total": 1,
                    "entries": [
                        {
                            "name": "电影",
                            "path": "/115网盘/电影",
                            "is_dir": True,
                            "provider": "115",
                            "file_id": "100",
                            "parent_id": "0",
                            "is_virtual": False,
                            "size": None,
                            "mtime": None,
                        }
                    ],
                }

        fake_service = Fake115Service()
        monkeypatch.setattr(file_service, "_get_115_service", lambda: fake_service)

        current_path, parent_path, entries, total, _cfid, _pfid = await file_service.browse_directory_async(
            path="/115网盘",
            provider="115",
            file_id="0",
            page=2,
            page_size=10,
        )

        assert fake_service.calls == [
            {
                "path": "/115网盘",
                "file_id": "0",
                "page": 2,
                "page_size": 10,
            }
        ]
        assert current_path == "/115网盘/电影"
        assert parent_path == "/115网盘"
        assert total == 1
        assert len(entries) == 1
        assert entries[0].provider == "115"
        assert entries[0].file_id == "100"
        assert entries[0].parent_id == "0"
        assert entries[0].is_virtual is False

    @pytest.mark.asyncio
    async def test_browse_directory_async_provider_115_forwards_virtual_path_without_file_id(
        self,
        file_service: FileService,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """115 async browse should preserve the requested path when file_id is missing."""

        class Fake115Service:
            def __init__(self):
                self.calls = []

            async def browse(
                self,
                *,
                path: str,
                file_id: str | None,
                page: int,
                page_size: int,
            ):
                self.calls.append(
                    {
                        "path": path,
                        "file_id": file_id,
                        "page": page,
                        "page_size": page_size,
                    }
                )
                return {
                    "current_path": "/115网盘/电影",
                    "parent_path": "/115网盘",
                    "total": 0,
                    "entries": [],
                }

        fake_service = Fake115Service()
        monkeypatch.setattr(file_service, "_get_115_service", lambda: fake_service)

        current_path, parent_path, entries, total, _cfid, _pfid = await file_service.browse_directory_async(
            path="/115网盘/电影",
            provider="115",
            file_id=None,
            page=1,
            page_size=10,
        )

        assert fake_service.calls == [
            {
                "path": "/115网盘/电影",
                "file_id": None,
                "page": 1,
                "page_size": 10,
            }
        ]
        assert current_path == "/115网盘/电影"
        assert parent_path == "/115网盘"
        assert entries == []
        assert total == 0
