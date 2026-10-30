"""Manual job API endpoints."""
from fastapi import APIRouter, Depends, Query

from server.api.deps import get_manual_job_service, require_auth
from server.application.manual_job_service import ManualJobService
from server.models.manual_job import (
    ManualJob,
    ManualJobCreate,
    ManualJobDeleteRequest,
    ManualJobListResponse,
    ManualJobStatus,
)

router = APIRouter(prefix="/api/manual-jobs", tags=["manual-jobs"], dependencies=[Depends(require_auth)])


@router.post("", response_model=ManualJob)
async def create_job(
    job: ManualJobCreate,
    service: ManualJobService = Depends(get_manual_job_service),
) -> ManualJob:
    """Create a new manual job."""
    return await service.create_job(job)


@router.get("", response_model=ManualJobListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: ManualJobStatus | None = Query(None),
    service: ManualJobService = Depends(get_manual_job_service),
) -> ManualJobListResponse:
    """List manual jobs with pagination."""
    offset = (page - 1) * page_size
    jobs, total = await service.list_jobs(
        limit=page_size,
        offset=offset,
        search=search,
        status=status,
    )
    return ManualJobListResponse(jobs=jobs, total=total)


@router.get("/{job_id}", response_model=ManualJob)
async def get_job(
    job_id: int,
    service: ManualJobService = Depends(get_manual_job_service),
) -> ManualJob | None:
    """Get a manual job by ID."""
    return await service.get_job(job_id)


@router.delete("")
async def delete_jobs(
    request: ManualJobDeleteRequest,
    service: ManualJobService = Depends(get_manual_job_service),
) -> dict:
    """Delete manual jobs by IDs."""
    deleted = await service.delete_jobs(request.ids)
    return {"deleted": deleted}
