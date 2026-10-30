"""Scheduled task data models."""

from datetime import datetime

from pydantic import BaseModel


class ScheduledTask(BaseModel):
    """Scheduled task model."""

    id: str
    name: str
    folder_path: str
    cron_expression: str
    enabled: bool = True
    last_run: datetime | None = None
    next_run: datetime | None = None
    created_at: datetime | None = None


class ScheduledTaskCreate(BaseModel):
    """Request model for creating a scheduled task."""

    name: str
    folder_path: str
    cron_expression: str
    enabled: bool = True


class ScheduledTaskUpdate(BaseModel):
    """Request model for updating a scheduled task."""

    name: str | None = None
    folder_path: str | None = None
    cron_expression: str | None = None
    enabled: bool | None = None


class ScheduledTaskResponse(BaseModel):
    """Response model for scheduled task."""

    id: str
    name: str
    folder_path: str
    cron_expression: str
    enabled: bool
    last_run: datetime | None
    next_run: datetime | None
    created_at: datetime | None


class ScheduledTaskListResponse(BaseModel):
    """Response model for scheduled task list."""

    tasks: list[ScheduledTaskResponse]
    total: int
