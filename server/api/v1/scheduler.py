"""Scheduler API endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from server.api.deps import get_scheduler_service, require_auth
from server.application.scheduler_service import SchedulerService
from server.models.scheduler import (
    ScheduledTaskCreate,
    ScheduledTaskListResponse,
    ScheduledTaskResponse,
    ScheduledTaskUpdate,
)

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"], dependencies=[Depends(require_auth)])


@router.get("", response_model=ScheduledTaskListResponse)
async def list_tasks(
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledTaskListResponse:
    """List all scheduled tasks."""
    tasks = await scheduler_service.list_tasks()
    return ScheduledTaskListResponse(
        tasks=[ScheduledTaskResponse(**t.model_dump()) for t in tasks],
        total=len(tasks),
    )


@router.post("", response_model=ScheduledTaskResponse)
async def create_task(
    task: ScheduledTaskCreate,
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledTaskResponse:
    """Create a new scheduled task."""
    created = await scheduler_service.create_task(task)
    return ScheduledTaskResponse(**created.model_dump())


@router.get("/{task_id}", response_model=ScheduledTaskResponse)
async def get_task(
    task_id: str,
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledTaskResponse:
    """Get a scheduled task by ID."""
    task = await scheduler_service.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return ScheduledTaskResponse(**task.model_dump())


@router.put("/{task_id}", response_model=ScheduledTaskResponse)
async def update_task(
    task_id: str,
    update: ScheduledTaskUpdate,
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledTaskResponse:
    """Update a scheduled task."""
    task = await scheduler_service.update_task(task_id, update)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return ScheduledTaskResponse(**task.model_dump())


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> dict:
    """Delete a scheduled task."""
    deleted = await scheduler_service.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "message": "任务已删除"}


@router.post("/{task_id}/toggle", response_model=ScheduledTaskResponse)
async def toggle_task(
    task_id: str,
    scheduler_service: SchedulerService = Depends(get_scheduler_service),
) -> ScheduledTaskResponse:
    """Toggle task enabled status."""
    task = await scheduler_service.toggle_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return ScheduledTaskResponse(**task.model_dump())
