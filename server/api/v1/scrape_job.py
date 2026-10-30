"""Scrape job API endpoints - 文件刮削任务 API"""
from fastapi import APIRouter, Depends, Query

from server.api.deps import get_scrape_job_service, require_auth
from server.application.scrape_job_service import ScrapeJobService
from server.models.scrape_job import (
    ScrapeJob,
    ScrapeJobCreate,
    ScrapeJobListResponse,
    ScrapeJobSource,
    ScrapeJobStatus,
)

router = APIRouter(prefix="/api/scrape-jobs", tags=["scrape-jobs"], dependencies=[Depends(require_auth)])


@router.post("", response_model=ScrapeJob)
async def create_job(
    job: ScrapeJobCreate,
    service: ScrapeJobService = Depends(get_scrape_job_service),
) -> ScrapeJob:
    """创建文件刮削任务"""
    return await service.create_job(job)


@router.get("", response_model=ScrapeJobListResponse)
async def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source: ScrapeJobSource | None = Query(None),
    source_id: int | None = Query(None),
    status: ScrapeJobStatus | None = Query(None),
    service: ScrapeJobService = Depends(get_scrape_job_service),
) -> ScrapeJobListResponse:
    """列出文件刮削任务"""
    offset = (page - 1) * page_size
    jobs, total = await service.list_jobs(
        limit=page_size,
        offset=offset,
        source=source,
        source_id=source_id,
        status=status,
    )
    return ScrapeJobListResponse(jobs=jobs, total=total)


@router.get("/{job_id}", response_model=ScrapeJob | None)
async def get_job(
    job_id: str,
    service: ScrapeJobService = Depends(get_scrape_job_service),
) -> ScrapeJob | None:
    """获取文件刮削任务详情"""
    return await service.get_job(job_id)


@router.delete("")
async def delete_jobs(
    ids: list[str] = Query(...),
    service: ScrapeJobService = Depends(get_scrape_job_service),
) -> dict:
    """删除文件刮削任务"""
    deleted = await service.delete_jobs(ids)
    return {"deleted": deleted}
