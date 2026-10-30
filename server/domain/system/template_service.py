"""Template service for file naming."""

import json
import re
from pathlib import Path
from typing import Any

from server.domain.system.config_service import NAMING_CONFIG_KEY
from server.infrastructure.db import DATABASE_PATH
from server.infrastructure.repositories.template_repository import TemplateRepository
from server.models.template import (
    NamingTemplate,
    TemplatePreviewResponse,
    TemplateValidationResult,
    TemplateVariable,
)

# Default sample data for preview
DEFAULT_SAMPLE_DATA = {
    "title": "权力的游戏",
    "original_title": "Game of Thrones",
    "year": 2011,
    "season": 1,
    "episode": 1,
    "episode_title": "凛冬将至",
    "air_date": "2011-04-17",
}

# Regex pattern to find template variables
VARIABLE_PATTERN = re.compile(r"\{(\w+)(?::[^}]+)?\}")

# Valid variable names
VALID_VARIABLES = {v.value for v in TemplateVariable}


class TemplateService:
    """Service for template parsing, validation, and preview."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the template service."""
        self._default_template = NamingTemplate()
        self._repo = TemplateRepository(db_path or DATABASE_PATH)

    def get_default_template(self) -> NamingTemplate:
        """Get the default naming template.

        Returns:
            Default naming template configuration.
        """
        return self._default_template

    def get_active_template(self) -> NamingTemplate:
        """Get the current naming template, falling back to defaults."""
        value = self._repo.get_config_value(NAMING_CONFIG_KEY)
        if value is None:
            return self._default_template

        try:
            data = json.loads(value)
            return NamingTemplate(**data)
        except (json.JSONDecodeError, ValueError):
            return self._default_template

    def validate_template(self, template: str) -> TemplateValidationResult:
        """Validate a template string.

        Args:
            template: Template string to validate.

        Returns:
            Validation result with status and found variables.
        """
        if not template or not template.strip():
            return TemplateValidationResult(
                valid=False,
                error="Template cannot be empty",
                variables_found=[],
            )

        # Find all variables in template
        variables_found = self._extract_variables(template)

        # Check for invalid variables
        invalid_vars = [v for v in variables_found if v not in VALID_VARIABLES]
        if invalid_vars:
            return TemplateValidationResult(
                valid=False,
                error=f"Invalid variables: {', '.join(invalid_vars)}",
                variables_found=variables_found,
            )

        # Try to format with sample data to check syntax
        try:
            self._format_template(template, DEFAULT_SAMPLE_DATA)
        except (KeyError, ValueError, IndexError) as e:
            return TemplateValidationResult(
                valid=False,
                error=f"Template format error: {e}",
                variables_found=variables_found,
            )

        return TemplateValidationResult(
            valid=True,
            error=None,
            variables_found=variables_found,
        )

    def preview_template(
        self,
        template: str,
        sample_data: dict[str, Any] | None = None,
    ) -> TemplatePreviewResponse:
        """Preview a template with sample data.

        Args:
            template: Template string to preview.
            sample_data: Optional sample data for preview.

        Returns:
            Preview response with formatted result.
        """
        # Validate first
        validation = self.validate_template(template)
        if not validation.valid:
            return TemplatePreviewResponse(
                template=template,
                preview="",
                valid=False,
                error=validation.error,
            )

        # Merge with default sample data
        data = {**DEFAULT_SAMPLE_DATA}
        if sample_data:
            data.update(sample_data)

        # Format the template
        try:
            preview = self._format_template(template, data)
            return TemplatePreviewResponse(
                template=template,
                preview=preview,
                valid=True,
            )
        except (KeyError, ValueError, IndexError) as e:
            return TemplatePreviewResponse(
                template=template,
                preview="",
                valid=False,
                error=f"Format error: {e}",
            )

    def format_filename(
        self,
        template: str,
        data: dict[str, Any],
    ) -> str:
        """Format a filename using the template and data.

        Args:
            template: Template string.
            data: Data dictionary with variable values.

        Returns:
            Formatted filename.

        Raises:
            ValueError: If template is invalid or data is missing.
        """
        validation = self.validate_template(template)
        if not validation.valid:
            raise ValueError(f"Invalid template: {validation.error}")

        return self._format_template(template, data)

    def _extract_variables(self, template: str) -> list[str]:
        """Extract variable names from a template.

        Args:
            template: Template string.

        Returns:
            List of variable names found.
        """
        matches = VARIABLE_PATTERN.findall(template)
        # Return unique variables in order of appearance
        seen = set()
        result = []
        for var in matches:
            if var not in seen:
                seen.add(var)
                result.append(var)
        return result

    def _format_template(self, template: str, data: dict[str, Any]) -> str:
        """Format a template with data.

        Args:
            template: Template string.
            data: Data dictionary.

        Returns:
            Formatted string.
        """
        return template.format(**data)

    def sanitize_filename(self, filename: str) -> str:
        """Sanitize a filename by removing invalid characters.

        Args:
            filename: Filename to sanitize.

        Returns:
            Sanitized filename safe for filesystem.
        """
        # Characters not allowed in Windows filenames
        invalid_chars = r'<>:"/\|?*'
        result = filename
        for char in invalid_chars:
            result = result.replace(char, "")

        # Remove leading/trailing spaces and dots
        result = result.strip(" .")

        # Replace multiple spaces with single space
        result = re.sub(r"\s+", " ", result)

        return result
