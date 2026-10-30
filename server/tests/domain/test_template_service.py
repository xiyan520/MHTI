"""Unit tests for TemplateService."""

import pytest

from server.domain.system.template_service import VALID_VARIABLES, TemplateService


@pytest.fixture
def template_service():
    """Provide a TemplateService instance."""
    return TemplateService()


class TestTemplateServiceBasic:
    """Basic tests for TemplateService."""

    def test_get_default_template(self, template_service):
        """Test getting default template."""
        template = template_service.get_default_template()

        assert template.series_folder == "{title} ({year})"
        assert template.season_folder == "Season {season}"
        assert "{season:02d}" in template.episode_file
        assert "{episode:02d}" in template.episode_file

    def test_valid_variables(self):
        """Test that valid variables are defined."""
        assert "title" in VALID_VARIABLES
        assert "season" in VALID_VARIABLES
        assert "episode" in VALID_VARIABLES
        assert "episode_title" in VALID_VARIABLES
        assert "year" in VALID_VARIABLES
        assert "original_title" in VALID_VARIABLES
        assert "air_date" in VALID_VARIABLES


class TestTemplateValidation:
    """Tests for template validation."""

    def test_validate_valid_template(self, template_service):
        """Test validating a valid template."""
        result = template_service.validate_template("{title} - S{season:02d}E{episode:02d}")

        assert result.valid is True
        assert result.error is None
        assert "title" in result.variables_found
        assert "season" in result.variables_found
        assert "episode" in result.variables_found

    def test_validate_empty_template(self, template_service):
        """Test validating empty template."""
        result = template_service.validate_template("")

        assert result.valid is False
        assert "empty" in result.error.lower()

    def test_validate_whitespace_template(self, template_service):
        """Test validating whitespace-only template."""
        result = template_service.validate_template("   ")

        assert result.valid is False
        assert "empty" in result.error.lower()

    def test_validate_invalid_variable(self, template_service):
        """Test validating template with invalid variable."""
        result = template_service.validate_template("{title} - {invalid_var}")

        assert result.valid is False
        assert "invalid" in result.error.lower()
        assert "invalid_var" in result.error

    def test_validate_simple_text_template(self, template_service):
        """Test validating template with no variables."""
        result = template_service.validate_template("simple text")

        assert result.valid is True
        assert result.variables_found == []

    def test_validate_malformed_format_spec(self, template_service):
        """Test validating template with malformed format spec."""
        result = template_service.validate_template("{season:invalid}")

        assert result.valid is False
        assert "error" in result.error.lower()

    def test_validate_duplicate_variables(self, template_service):
        """Test that duplicate variables are reported once."""
        result = template_service.validate_template("{title} - {title} - {episode}")

        assert result.valid is True
        # Should contain unique variables
        assert result.variables_found.count("title") == 1


class TestTemplatePreview:
    """Tests for template preview."""

    def test_preview_valid_template(self, template_service):
        """Test previewing a valid template."""
        result = template_service.preview_template("{title} - S{season:02d}E{episode:02d}")

        assert result.valid is True
        assert result.preview == "权力的游戏 - S01E01"
        assert result.error is None

    def test_preview_with_custom_data(self, template_service):
        """Test previewing with custom sample data."""
        result = template_service.preview_template(
            "{title} - S{season:02d}E{episode:02d}",
            sample_data={"title": "自定义剧名", "season": 2, "episode": 5},
        )

        assert result.valid is True
        assert result.preview == "自定义剧名 - S02E05"

    def test_preview_invalid_template(self, template_service):
        """Test previewing an invalid template."""
        result = template_service.preview_template("{invalid_var}")

        assert result.valid is False
        assert result.preview == ""
        assert result.error is not None

    def test_preview_with_episode_title(self, template_service):
        """Test previewing template with episode title."""
        result = template_service.preview_template(
            "{title} - S{season:02d}E{episode:02d} - {episode_title}"
        )

        assert result.valid is True
        assert "凛冬将至" in result.preview

    def test_preview_series_folder_template(self, template_service):
        """Test previewing series folder template."""
        result = template_service.preview_template("{title} ({year})")

        assert result.valid is True
        assert result.preview == "权力的游戏 (2011)"


class TestFormatFilename:
    """Tests for format_filename method."""

    def test_format_filename_basic(self, template_service):
        """Test basic filename formatting."""
        result = template_service.format_filename(
            "{title} - S{season:02d}E{episode:02d}",
            {"title": "测试剧集", "season": 1, "episode": 5},
        )

        assert result == "测试剧集 - S01E05"

    def test_format_filename_invalid_template(self, template_service):
        """Test formatting with invalid template."""
        with pytest.raises(ValueError):
            template_service.format_filename(
                "{invalid}",
                {"title": "test"},
            )

    def test_format_filename_missing_data(self, template_service):
        """Test formatting with missing data for valid variable."""
        # Note: {episode_title} is valid but not provided in data
        with pytest.raises(KeyError):
            template_service.format_filename(
                "{title} - {episode_title}",
                {"title": "test"},  # missing episode_title
            )


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_sanitize_removes_invalid_chars(self, template_service):
        """Test that invalid characters are removed."""
        result = template_service.sanitize_filename('Test: File / Name? <invalid>')

        assert ":" not in result
        assert "/" not in result
        assert "?" not in result
        assert "<" not in result
        assert ">" not in result

    def test_sanitize_removes_quotes(self, template_service):
        """Test that quotes are removed."""
        result = template_service.sanitize_filename('Test "File" Name')

        assert '"' not in result

    def test_sanitize_trims_spaces_and_dots(self, template_service):
        """Test that leading/trailing spaces and dots are trimmed."""
        result = template_service.sanitize_filename("  .filename.  ")

        assert not result.startswith(" ")
        assert not result.startswith(".")
        assert not result.endswith(" ")
        assert not result.endswith(".")

    def test_sanitize_collapses_multiple_spaces(self, template_service):
        """Test that multiple spaces are collapsed."""
        result = template_service.sanitize_filename("Test    Multiple   Spaces")

        assert "  " not in result
        assert result == "Test Multiple Spaces"

    def test_sanitize_preserves_valid_chars(self, template_service):
        """Test that valid characters are preserved."""
        result = template_service.sanitize_filename("Test - S01E01 [1080p]")

        assert result == "Test - S01E01 [1080p]"
