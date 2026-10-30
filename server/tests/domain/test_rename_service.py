"""Unit tests for RenameService."""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from server.domain.artifacts.rename_service import RenameService
from server.domain.system.config_service import ConfigService
from server.domain.system.template_service import TemplateService
from server.models.organize import OrganizeMode
from server.models.rename import BatchRenameRequest, RenameRequest
from server.models.scraper import ScrapeByIdRequest, ScrapeStatus
from server.models.storage import StorageLocator, StorageProvider
from server.models.template import NamingTemplate
from server.models.tmdb import TMDBEpisode, TMDBSeason, TMDBSeries


@pytest.fixture
def rename_service(temp_db):
    """Provide a RenameService instance."""
    return RenameService(template_service=TemplateService(db_path=temp_db))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_video(temp_dir):
    """Create a sample video file for testing."""
    video_path = Path(temp_dir) / "test_video.mp4"
    video_path.write_bytes(b"fake video content")
    return str(video_path)


class TestRenameServicePreview:
    """Tests for preview_rename method."""

    def test_preview_basic(self, rename_service, sample_video):
        """Test basic preview functionality."""
        request = RenameRequest(
            source_path=sample_video,
            title="权力的游戏",
            season=1,
            episode=1,
            episode_title="凛冬将至",
        )

        preview = rename_service.preview_rename(request)

        assert preview.source_path == sample_video
        assert "权力的游戏" in preview.dest_path
        assert "S01E01" in preview.dest_path
        assert "凛冬将至" in preview.new_filename
        assert ".mp4" in preview.new_filename

    def test_preview_with_output_dir(self, rename_service, sample_video, temp_dir):
        """Test preview with custom output directory."""
        output_dir = Path(temp_dir) / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="Breaking Bad",
            season=5,
            episode=16,
            output_dir=str(output_dir),
        )

        preview = rename_service.preview_rename(request)

        assert str(output_dir) in preview.dest_path
        assert "Breaking Bad" in preview.dest_path
        assert "Season 5" in preview.dest_folder  # Default template uses {season} not {season:02d}
        assert "S05E16" in preview.new_filename

    def test_preview_creates_dirs_list(self, rename_service, sample_video, temp_dir):
        """Test that preview lists directories to be created."""
        output_dir = Path(temp_dir) / "new_output"
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(output_dir),
        )

        preview = rename_service.preview_rename(request)

        assert len(preview.will_create_dirs) > 0
        assert any("Test Show" in d for d in preview.will_create_dirs)

    def test_preview_without_episode_title(self, rename_service, sample_video):
        """Test preview without episode title."""
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=2,
            episode=5,
        )

        preview = rename_service.preview_rename(request)

        assert "S02E05" in preview.new_filename
        assert ".mp4" in preview.new_filename

    @pytest.mark.asyncio
    async def test_preview_uses_saved_naming_template(self, temp_dir, sample_video):
        """Test preview uses persisted naming template configuration."""
        db_path = Path(temp_dir) / "config.db"
        config_service = ConfigService(db_path=db_path)
        await config_service.save_naming_config(
            NamingTemplate(
                series_folder="{title}",
                season_folder="S{season:02d}",
                episode_file="{title}.S{season:02d}E{episode:02d}",
            )
        )
        rename_service = RenameService(template_service=TemplateService(db_path=db_path))

        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=2,
            episode=5,
            output_dir=temp_dir,
        )

        preview = rename_service.preview_rename(request)

        assert preview.dest_folder.endswith(str(Path("Test Show") / "S02"))
        assert preview.new_filename == "Test Show.S02E05.mp4"


class TestRenameServiceExecute:
    """Tests for execute_rename method."""

    def test_execute_rename_success(self, rename_service, sample_video, temp_dir):
        """Test successful rename execution."""
        output_dir = Path(temp_dir) / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(output_dir),
        )

        result = rename_service.execute_rename(request)

        assert result.success is True
        assert Path(result.dest_path).exists()
        assert not Path(sample_video).exists()  # Original moved

    def test_execute_rename_with_backup(self, rename_service, sample_video, temp_dir):
        """Test rename with backup creation."""
        output_dir = Path(temp_dir) / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(output_dir),
        )

        result = rename_service.execute_rename(request, create_backup=True)

        assert result.success is True
        assert result.backup_path is not None
        assert Path(result.backup_path).exists()

    def test_execute_rename_source_not_found(self, rename_service, temp_dir):
        """Test rename with non-existent source."""
        request = RenameRequest(
            source_path="/nonexistent/video.mp4",
            title="Test Show",
            season=1,
            episode=1,
        )

        result = rename_service.execute_rename(request)

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_execute_rename_destination_exists(self, rename_service, temp_dir):
        """Test rename when destination already exists."""
        # Create source file
        source_path = Path(temp_dir) / "source.mp4"
        source_path.write_bytes(b"source content")

        # First, do a preview to get exact dest filename
        request = RenameRequest(
            source_path=str(source_path),
            title="Test Show",
            season=1,
            episode=1,
            output_dir=str(Path(temp_dir) / "output"),
        )
        preview = rename_service.preview_rename(request)

        # Create destination structure with the exact expected file
        dest_path = Path(preview.dest_path)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"existing content")

        result = rename_service.execute_rename(request)

        assert result.success is False
        assert "exists" in result.error.lower()

    def test_execute_creates_directory_structure(self, rename_service, sample_video, temp_dir):
        """Test that execute creates necessary directories."""
        output_dir = Path(temp_dir) / "deep" / "nested" / "output"
        request = RenameRequest(
            source_path=sample_video,
            title="My Show",
            season=3,
            episode=10,
            output_dir=str(output_dir),
        )

        result = rename_service.execute_rename(request)

        assert result.success is True
        assert Path(result.dest_path).parent.exists()


class TestRenameServiceBatch:
    """Tests for batch_rename method."""

    def test_batch_rename_success(self, rename_service, temp_dir):
        """Test successful batch rename."""
        # Create multiple source files
        items = []
        for i in range(3):
            source_path = Path(temp_dir) / f"video{i}.mp4"
            source_path.write_bytes(b"video content")
            items.append(
                RenameRequest(
                    source_path=str(source_path),
                    title="Test Show",
                    season=1,
                    episode=i + 1,
                    output_dir=str(Path(temp_dir) / "output"),
                )
            )

        request = BatchRenameRequest(items=items)
        response = rename_service.batch_rename(request)

        assert response.total == 3
        assert response.success == 3
        assert response.failed == 0

    def test_batch_rename_dry_run(self, rename_service, temp_dir):
        """Test batch rename in dry-run mode."""
        # Create source files
        items = []
        for i in range(2):
            source_path = Path(temp_dir) / f"video{i}.mp4"
            source_path.write_bytes(b"video content")
            items.append(
                RenameRequest(
                    source_path=str(source_path),
                    title="Test Show",
                    season=1,
                    episode=i + 1,
                    output_dir=str(Path(temp_dir) / "output"),
                )
            )

        request = BatchRenameRequest(items=items, dry_run=True)
        response = rename_service.batch_rename(request)

        assert response.total == 2
        assert response.success == 2
        assert response.previews is not None
        assert len(response.previews) == 2
        # Original files should still exist (dry run)
        for item in items:
            assert Path(item.source_path).exists()

    def test_batch_rename_partial_failure(self, rename_service, temp_dir):
        """Test batch rename with some failures."""
        # Create one valid file
        valid_path = Path(temp_dir) / "valid.mp4"
        valid_path.write_bytes(b"video content")

        items = [
            RenameRequest(
                source_path=str(valid_path),
                title="Test Show",
                season=1,
                episode=1,
                output_dir=str(Path(temp_dir) / "output"),
            ),
            RenameRequest(
                source_path="/nonexistent/video.mp4",
                title="Test Show",
                season=1,
                episode=2,
                output_dir=str(Path(temp_dir) / "output"),
            ),
        ]

        request = BatchRenameRequest(items=items)
        response = rename_service.batch_rename(request)

        assert response.total == 2
        assert response.success == 1
        assert response.failed == 1

    def test_batch_rename_empty(self, rename_service):
        """Test batch rename with empty list."""
        request = BatchRenameRequest(items=[])
        response = rename_service.batch_rename(request)

        assert response.total == 0
        assert response.success == 0
        assert response.failed == 0


class TestRenameServiceHelpers:
    """Tests for helper methods."""

    def test_create_series_structure(self, rename_service, temp_dir):
        """Test creating series folder structure."""
        created = rename_service.create_series_structure(
            output_dir=temp_dir,
            title="My Test Show",
            seasons=[1, 2, 3],
        )

        assert len(created) == 4  # 1 series + 3 seasons
        assert Path(temp_dir) / "My Test Show" in [Path(d) for d in created]
        assert (Path(temp_dir) / "My Test Show" / "Season 01").exists()
        assert (Path(temp_dir) / "My Test Show" / "Season 02").exists()
        assert (Path(temp_dir) / "My Test Show" / "Season 03").exists()

    def test_create_series_structure_no_seasons(self, rename_service, temp_dir):
        """Test creating series structure without seasons."""
        created = rename_service.create_series_structure(
            output_dir=temp_dir,
            title="Another Show",
        )

        assert len(created) == 1
        assert (Path(temp_dir) / "Another Show").exists()

    def test_create_series_structure_sanitizes_title(self, rename_service, temp_dir):
        """Test that series structure sanitizes invalid characters."""
        created = rename_service.create_series_structure(
            output_dir=temp_dir,
            title="Show: With/Invalid?Chars",
        )

        # Should create directory without invalid characters
        assert len(created) == 1
        created_path = Path(created[0])
        assert created_path.exists()
        assert ":" not in created_path.name
        assert "/" not in created_path.name
        assert "?" not in created_path.name


class Test115OutputBranches:
    """Tests for provider-aware 115 output handling."""

    @staticmethod
    def _build_locator(
        *,
        provider: StorageProvider,
        path: str,
        file_id: str | None = None,
        parent_id: str | None = None,
        is_dir: bool,
    ) -> StorageLocator:
        return StorageLocator(
            provider=provider,
            path=path,
            file_id=file_id,
            parent_id=parent_id,
            is_dir=is_dir,
        )

    @pytest.mark.asyncio
    async def test_scraper_115_to_115_uses_provider_move(self, monkeypatch, temp_db):
        """115 source and 115 target should use provider-native move/copy path."""
        from server.application.scraping.service import ScraperService

        class FakeProvider:
            def __init__(self):
                self.ensure_calls = []
                self.rename_calls = []
                self.copy_calls = []

            async def ensure_directory(self, locator, relative_path):
                self.ensure_calls.append((locator, relative_path))
                return {"id": "season-dir", "parent_id": locator.file_id}

            async def rename(self, locator, target_name, target_parent_id):
                self.rename_calls.append((locator, target_name, target_parent_id))
                return {"state": True}

            async def copy(self, locator, target_name, target_parent_id):
                self.copy_calls.append((locator, target_name, target_parent_id))
                return {"state": True}

        provider = FakeProvider()

        service = ScraperService(
            config_service=ConfigService(db_path=temp_db),
            tmdb_service=None,
            parser_service=None,
            nfo_service=None,
            rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)

        file_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/episode.mp4",
            file_id="file-001",
            parent_id="scan-root",
            is_dir=False,
        )
        output_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/已整理",
            file_id="target-root",
            parent_id="0",
            is_dir=True,
        )

        result = await service._finalize_storage_output(
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=None,
            link_mode=OrganizeMode.MOVE,
            title="Test Show",
            season=1,
            episode=1,
            source_path=file_locator.path,
        )

        assert result.provider == StorageProvider.P115
        assert provider.ensure_calls
        assert provider.rename_calls
        assert not provider.copy_calls

    @pytest.mark.asyncio
    async def test_scraper_115_to_local_downloads_before_organize(
        self,
        monkeypatch,
        temp_db,
        tmp_path: Path,
    ):
        """115 source to local target should download the file before local organize."""
        from server.application.scraping.service import ScraperService

        downloaded_file = tmp_path / "downloaded.mp4"
        downloaded_file.write_bytes(b"downloaded")

        class FakeProvider:
            def __init__(self):
                self.download = AsyncMock(return_value=downloaded_file)

        provider = FakeProvider()

        service = ScraperService(
            config_service=ConfigService(db_path=temp_db),
            tmdb_service=None,
            parser_service=None,
            nfo_service=None,
            rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)

        file_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/episode.mp4",
            file_id="file-001",
            parent_id="scan-root",
            is_dir=False,
        )
        output_locator = self._build_locator(
            provider=StorageProvider.LOCAL,
            path=str(tmp_path / "library"),
            is_dir=True,
        )

        result = await service._finalize_storage_output(
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_locator=None,
            link_mode=OrganizeMode.COPY,
            title="Test Show",
            season=1,
            episode=1,
            source_path=file_locator.path,
        )

        provider.download.assert_awaited_once()
        assert result.provider == StorageProvider.LOCAL
        assert Path(result.path) == Path(output_locator.path)

    @pytest.mark.asyncio
    async def test_scrape_by_id_115_to_local_organizes_download_once(
        self,
        monkeypatch,
        temp_db,
        tmp_path: Path,
    ):
        """scrape_by_id for 115->local should download once and organize exactly once."""
        from server.application.scraping.service import ScraperService

        downloaded_file = tmp_path / "downloaded.mp4"
        downloaded_file.write_bytes(b"downloaded")

        class FakeProvider:
            def __init__(self):
                self.download = AsyncMock(return_value=downloaded_file)

        class FakeTMDBService:
            def __init__(self):
                self.get_series_by_api = AsyncMock(
                    return_value=TMDBSeries(
                        id=123,
                        name="Test Show",
                        original_name="Test Show",
                        first_air_date=date(2024, 1, 1),
                        number_of_seasons=1,
                        number_of_episodes=1,
                        seasons=[TMDBSeason(season_number=1, name="Season 1")],
                    )
                )
                self.get_season_by_api = AsyncMock(
                    return_value=TMDBSeason(
                        season_number=1,
                        name="Season 1",
                        episodes=[
                            TMDBEpisode(
                                episode_number=1,
                                name="Episode 1",
                            )
                        ],
                    )
                )

        provider = FakeProvider()
        tmdb_service = FakeTMDBService()

        service = ScraperService(
            config_service=ConfigService(db_path=temp_db),
            tmdb_service=tmdb_service,
            parser_service=None,
            nfo_service=None,
            rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
            image_service=None,
            subtitle_service=None,
            emby_service=None,
        )
        monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)
        monkeypatch.setattr(service, "_generate_episode_nfo", lambda *args, **kwargs: "<episode />")
        monkeypatch.setattr(
            service,
            "_get_effective_nfo_config",
            AsyncMock(return_value={"nfo_enabled": False}),
        )
        monkeypatch.setattr(
            service,
            "_get_effective_download_config",
            AsyncMock(
                return_value={
                    "download_poster": False,
                    "download_fanart": False,
                    "download_thumb": False,
                }
            ),
        )
        monkeypatch.setattr(service, "_process_subtitles", lambda *args, **kwargs: [])

        file_locator = self._build_locator(
            provider=StorageProvider.P115,
            path="/115网盘/待整理/episode.mp4",
            file_id="file-001",
            parent_id="scan-root",
            is_dir=False,
        )
        output_locator = self._build_locator(
            provider=StorageProvider.LOCAL,
            path=str(tmp_path / "library"),
            is_dir=True,
        )

        result = await service.scrape_by_id(
            ScrapeByIdRequest(
                file_path=file_locator.path,
                tmdb_id=123,
                season=1,
                episode=1,
                file_locator=file_locator,
                output_locator=output_locator,
                link_mode=OrganizeMode.COPY,
            )
        )

        expected_folder = Path(output_locator.path) / "Test Show (2024)" / "Season 1"

        provider.download.assert_awaited_once()
        assert result.status == ScrapeStatus.SUCCESS
        assert result.dest_path is not None
        assert Path(result.dest_path).parent == expected_folder
        assert Path(result.dest_path).exists()


# ============================================================================
# 原子落盘：失败/取消不留半成品
# ============================================================================


class TestAtomicFileOperations:
    """复制/移动必须「全成或全无」。

    断点重试的前提是：目标目录里要么是完整文件，要么什么都没有；留下半截 mkv
    会被下一次执行当成「已存在」直接判冲突。
    """

    def test_copy_failure_leaves_no_partial_file(self, rename_service, tmp_path, monkeypatch):
        src = tmp_path / "src.mkv"
        src.write_bytes(b"payload")
        dst = tmp_path / "dst.mkv"

        def boom(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr("server.domain.artifacts.rename_service.shutil.copy2", boom)

        with pytest.raises(OSError):
            rename_service._execute_file_operation(src, dst, OrganizeMode.COPY)

        assert not dst.exists()
        assert list(tmp_path.glob("*.mhti-tmp-*")) == []

    def test_copy_success_produces_complete_file(self, rename_service, tmp_path):
        src = tmp_path / "src.mkv"
        src.write_bytes(b"payload")
        dst = tmp_path / "dst.mkv"

        rename_service._execute_file_operation(src, dst, OrganizeMode.COPY)

        assert dst.read_bytes() == b"payload"
        # 复制模式源文件保留
        assert src.exists()
        assert list(tmp_path.glob("*.mhti-tmp-*")) == []

    def test_move_across_devices_falls_back_to_copy_then_unlink(
        self, rename_service, tmp_path, monkeypatch
    ):
        import errno as errno_module

        import server.domain.artifacts.rename_service as rename_module

        src = tmp_path / "src.mkv"
        src.write_bytes(b"payload")
        dst = tmp_path / "dst.mkv"
        real_replace = rename_module.os.replace

        def fake_replace(source, target):
            # 模拟同盘 rename 不可用（跨盘 EXDEV），临时文件替换仍应正常
            if Path(source) == src:
                raise OSError(errno_module.EXDEV, "Invalid cross-device link")
            return real_replace(source, target)

        monkeypatch.setattr(rename_module.os, "replace", fake_replace)

        rename_service._execute_file_operation(src, dst, OrganizeMode.MOVE)

        assert dst.read_bytes() == b"payload"
        assert not src.exists()
        assert list(tmp_path.glob("*.mhti-tmp-*")) == []
