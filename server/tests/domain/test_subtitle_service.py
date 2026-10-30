"""Unit tests for SubtitleService."""

import tempfile
from pathlib import Path

import pytest

from server.domain.artifacts.subtitle_service import SUBTITLE_EXTENSIONS, SubtitleService
from server.models.subtitle import SubtitleLanguage


@pytest.fixture
def subtitle_service():
    """Provide a SubtitleService instance."""
    return SubtitleService()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class TestSubtitleServiceScan:
    """Tests for scan_subtitles method."""

    def test_scan_empty_folder(self, subtitle_service, temp_dir):
        """Test scanning empty folder."""
        result = subtitle_service.scan_subtitles(temp_dir)

        assert result.total == 0
        assert result.subtitles == []

    def test_scan_finds_subtitles(self, subtitle_service, temp_dir):
        """Test scanning finds subtitle files."""
        # Create subtitle files
        (Path(temp_dir) / "video.srt").write_text("subtitle content")
        (Path(temp_dir) / "video.ass").write_text("subtitle content")
        (Path(temp_dir) / "video.mp4").write_bytes(b"video content")  # Should be ignored

        result = subtitle_service.scan_subtitles(temp_dir)

        assert result.total == 2
        extensions = {s.extension for s in result.subtitles}
        assert ".srt" in extensions
        assert ".ass" in extensions

    def test_scan_all_supported_formats(self, subtitle_service, temp_dir):
        """Test scanning all supported subtitle formats."""
        for ext in SUBTITLE_EXTENSIONS:
            (Path(temp_dir) / f"video{ext}").write_text("content")

        result = subtitle_service.scan_subtitles(temp_dir)

        assert result.total == len(SUBTITLE_EXTENSIONS)

    def test_scan_nonexistent_folder(self, subtitle_service):
        """Test scanning non-existent folder."""
        result = subtitle_service.scan_subtitles("/nonexistent/folder")

        assert result.total == 0
        assert result.subtitles == []

    def test_scan_recursive(self, subtitle_service, temp_dir):
        """Test scanning recursively finds subtitles in subfolders."""
        subdir = Path(temp_dir) / "Season 1"
        subdir.mkdir()
        (subdir / "EP01.srt").write_text("content")
        (Path(temp_dir) / "root.srt").write_text("content")

        result = subtitle_service.scan_subtitles(temp_dir)

        assert result.total == 2


class TestSubtitleServiceLanguage:
    """Tests for language extraction."""

    def test_extract_language_chs(self, subtitle_service):
        """Test extracting Chinese simplified language."""
        cases = [
            "video.chs.srt",
            "video.sc.srt",
            "video.zh.srt",
            "video.chi.srt",
        ]
        for filename in cases:
            path = Path(f"/tmp/{filename}")
            result = subtitle_service._parse_subtitle_file(path)
            assert result.language == SubtitleLanguage.CHS, f"Failed for {filename}"

    def test_extract_language_cht(self, subtitle_service):
        """Test extracting Chinese traditional language."""
        cases = [
            "video.cht.srt",
            "video.tc.srt",
        ]
        for filename in cases:
            path = Path(f"/tmp/{filename}")
            result = subtitle_service._parse_subtitle_file(path)
            assert result.language == SubtitleLanguage.CHT, f"Failed for {filename}"

    def test_extract_language_eng(self, subtitle_service):
        """Test extracting English language."""
        cases = [
            "video.eng.srt",
            "video.en.srt",
        ]
        for filename in cases:
            path = Path(f"/tmp/{filename}")
            result = subtitle_service._parse_subtitle_file(path)
            assert result.language == SubtitleLanguage.ENG, f"Failed for {filename}"

    def test_extract_language_jpn(self, subtitle_service):
        """Test extracting Japanese language."""
        cases = [
            "video.jpn.srt",
            "video.ja.srt",
            "video.jap.srt",
        ]
        for filename in cases:
            path = Path(f"/tmp/{filename}")
            result = subtitle_service._parse_subtitle_file(path)
            assert result.language == SubtitleLanguage.JPN, f"Failed for {filename}"

    def test_extract_language_none(self, subtitle_service):
        """Test when no language tag present."""
        path = Path("/tmp/video.srt")
        result = subtitle_service._parse_subtitle_file(path)
        assert result.language is None

    def test_extract_language_case_insensitive(self, subtitle_service):
        """Test language extraction is case insensitive."""
        path = Path("/tmp/video.CHS.srt")
        result = subtitle_service._parse_subtitle_file(path)
        assert result.language == SubtitleLanguage.CHS


class TestSubtitleServiceAssociate:
    """Tests for associate_subtitles method."""

    def test_associate_matching_names(self, subtitle_service, temp_dir):
        """Test associating subtitles with matching video names."""
        # Create files
        (Path(temp_dir) / "EP01.mp4").write_bytes(b"video")
        (Path(temp_dir) / "EP01.chs.srt").write_text("subtitle")
        (Path(temp_dir) / "EP01.eng.srt").write_text("subtitle")

        result = subtitle_service.associate_subtitles(temp_dir)

        assert len(result.associations) == 1
        assert result.associations[0].video == "EP01.mp4"
        assert len(result.associations[0].subtitles) == 2

    def test_associate_no_matches(self, subtitle_service, temp_dir):
        """Test when no subtitles match videos."""
        (Path(temp_dir) / "video1.mp4").write_bytes(b"video")
        (Path(temp_dir) / "video2.srt").write_text("subtitle")

        result = subtitle_service.associate_subtitles(temp_dir)

        assert len(result.associations) == 1
        assert len(result.associations[0].subtitles) == 0

    def test_associate_specific_videos(self, subtitle_service, temp_dir):
        """Test associating with specific video list."""
        (Path(temp_dir) / "EP01.srt").write_text("subtitle")
        (Path(temp_dir) / "EP02.srt").write_text("subtitle")

        result = subtitle_service.associate_subtitles(
            temp_dir,
            video_files=["EP01.mp4"],
        )

        assert len(result.associations) == 1
        assert result.associations[0].video == "EP01.mp4"
        assert len(result.associations[0].subtitles) == 1

    def test_associate_normalized_names(self, subtitle_service, temp_dir):
        """Test associating with normalized name matching."""
        (Path(temp_dir) / "Show.Name.S01E01.mp4").write_bytes(b"video")
        (Path(temp_dir) / "Show_Name_S01E01.chs.srt").write_text("subtitle")

        result = subtitle_service.associate_subtitles(temp_dir)

        assert len(result.associations) == 1
        assert len(result.associations[0].subtitles) == 1


class TestSubtitleServiceRename:
    """Tests for rename_subtitle method."""

    def test_rename_success(self, subtitle_service, temp_dir):
        """Test successful subtitle rename."""
        source = Path(temp_dir) / "EP01.chs.srt"
        source.write_text("subtitle content")

        result = subtitle_service.rename_subtitle(
            str(source),
            "Show Name - S01E01 - Title",
        )

        assert result.success is True
        assert "Show Name - S01E01 - Title.chs.srt" in result.dest_path
        assert Path(result.dest_path).exists()
        assert not source.exists()

    def test_rename_preserve_language(self, subtitle_service, temp_dir):
        """Test rename preserves language tag."""
        source = Path(temp_dir) / "video.eng.srt"
        source.write_text("subtitle")

        result = subtitle_service.rename_subtitle(
            str(source),
            "New Name",
            preserve_language=True,
        )

        assert result.success is True
        assert ".eng.srt" in result.dest_path

    def test_rename_no_preserve_language(self, subtitle_service, temp_dir):
        """Test rename without preserving language."""
        source = Path(temp_dir) / "video.chs.srt"
        source.write_text("subtitle")

        result = subtitle_service.rename_subtitle(
            str(source),
            "New Name",
            preserve_language=False,
        )

        assert result.success is True
        assert result.dest_path.endswith("New Name.srt")

    def test_rename_not_found(self, subtitle_service):
        """Test rename with non-existent file."""
        result = subtitle_service.rename_subtitle(
            "/nonexistent/file.srt",
            "New Name",
        )

        assert result.success is False
        assert "not found" in result.error.lower()

    def test_rename_destination_exists(self, subtitle_service, temp_dir):
        """Test rename when destination exists."""
        source = Path(temp_dir) / "source.srt"
        source.write_text("source")
        dest = Path(temp_dir) / "New Name.srt"
        dest.write_text("existing")

        result = subtitle_service.rename_subtitle(str(source), "New Name")

        assert result.success is False
        assert "exists" in result.error.lower()


class TestSubtitleServiceBatch:
    """Tests for batch_rename_subtitles method."""

    def test_batch_rename_success(self, subtitle_service, temp_dir):
        """Test successful batch rename."""
        for i in range(3):
            (Path(temp_dir) / f"EP0{i}.chs.srt").write_text("subtitle")

        items = [
            (str(Path(temp_dir) / f"EP0{i}.chs.srt"), f"Show - S01E0{i}", True)
            for i in range(3)
        ]

        result = subtitle_service.batch_rename_subtitles(items)

        assert result.total == 3
        assert result.success == 3
        assert result.failed == 0

    def test_batch_rename_partial_failure(self, subtitle_service, temp_dir):
        """Test batch rename with partial failure."""
        (Path(temp_dir) / "EP01.srt").write_text("subtitle")

        items = [
            (str(Path(temp_dir) / "EP01.srt"), "Show - S01E01", True),
            ("/nonexistent/file.srt", "Show - S01E02", True),
        ]

        result = subtitle_service.batch_rename_subtitles(items)

        assert result.total == 2
        assert result.success == 1
        assert result.failed == 1

    def test_batch_rename_empty(self, subtitle_service):
        """Test batch rename with empty list."""
        result = subtitle_service.batch_rename_subtitles([])

        assert result.total == 0
        assert result.success == 0
        assert result.failed == 0
