"""Subtitle API endpoints."""

from fastapi import APIRouter, Depends

from server.api.deps import get_subtitle_service, require_auth
from server.domain.artifacts.subtitle_service import SubtitleService
from server.models.subtitle import (
    BatchSubtitleRenameRequest,
    BatchSubtitleRenameResponse,
    SubtitleAssociateRequest,
    SubtitleAssociateResponse,
    SubtitleRenameRequest,
    SubtitleRenameResult,
    SubtitleScanRequest,
    SubtitleScanResponse,
)

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"], dependencies=[Depends(require_auth)])


@router.post("/scan", response_model=SubtitleScanResponse)
def scan_subtitles(
    request: SubtitleScanRequest,
    subtitle_service: SubtitleService = Depends(get_subtitle_service),
) -> SubtitleScanResponse:
    """
    Scan a folder for subtitle files.

    Args:
        request: Scan request with folder path.

    Returns:
        Response with list of found subtitle files.
    """
    return subtitle_service.scan_subtitles(request.folder_path)


@router.post("/associate", response_model=SubtitleAssociateResponse)
def associate_subtitles(
    request: SubtitleAssociateRequest,
    subtitle_service: SubtitleService = Depends(get_subtitle_service),
) -> SubtitleAssociateResponse:
    """
    Associate subtitle files with video files.

    Args:
        request: Association request with folder and optional video list.

    Returns:
        Response with video-subtitle associations.
    """
    return subtitle_service.associate_subtitles(
        folder_path=request.folder_path,
        video_files=request.video_files,
    )


@router.post("/rename", response_model=SubtitleRenameResult)
def rename_subtitle(
    request: SubtitleRenameRequest,
    subtitle_service: SubtitleService = Depends(get_subtitle_service),
) -> SubtitleRenameResult:
    """
    Rename a subtitle file to match a video file.

    Args:
        request: Rename request with subtitle path and new video name.

    Returns:
        Result of the rename operation.
    """
    return subtitle_service.rename_subtitle(
        subtitle_path=request.subtitle_path,
        new_video_name=request.new_video_name,
        preserve_language=request.preserve_language,
    )


@router.post("/rename/batch", response_model=BatchSubtitleRenameResponse)
def batch_rename_subtitles(
    request: BatchSubtitleRenameRequest,
    subtitle_service: SubtitleService = Depends(get_subtitle_service),
) -> BatchSubtitleRenameResponse:
    """
    Batch rename subtitle files.

    Args:
        request: Batch rename request with items.

    Returns:
        Batch rename response with all results.
    """
    items = [
        (item.subtitle_path, item.new_video_name, item.preserve_language)
        for item in request.items
    ]
    return subtitle_service.batch_rename_subtitles(items)
