"""Rename API endpoints."""

from fastapi import APIRouter, Depends

from server.api.deps import get_rename_service, require_auth
from server.domain.artifacts.rename_service import RenameService
from server.models.rename import (
    BatchRenameRequest,
    BatchRenameResponse,
    RenamePreview,
    RenameRequest,
    RenameResult,
)

router = APIRouter(prefix="/api/rename", tags=["rename"], dependencies=[Depends(require_auth)])


@router.post("/preview", response_model=RenamePreview)
def preview_rename(
    request: RenameRequest,
    rename_service: RenameService = Depends(get_rename_service),
) -> RenamePreview:
    """
    Preview a rename operation without executing it.

    Args:
        request: Rename request with source path and metadata.

    Returns:
        Preview of the rename operation with destination path.
    """
    return rename_service.preview_rename(request)


@router.post("/execute", response_model=RenameResult)
def execute_rename(
    request: RenameRequest,
    create_backup: bool = False,
    rename_service: RenameService = Depends(get_rename_service),
) -> RenameResult:
    """
    Execute a rename operation.

    Args:
        request: Rename request with source path and metadata.
        create_backup: Whether to create a backup of the original file.

    Returns:
        Result of the rename operation.
    """
    return rename_service.execute_rename(request, create_backup=create_backup)


@router.post("/batch", response_model=BatchRenameResponse)
def batch_rename(
    request: BatchRenameRequest,
    rename_service: RenameService = Depends(get_rename_service),
) -> BatchRenameResponse:
    """
    Execute batch rename operations.

    Args:
        request: Batch rename request with items and options.

    Returns:
        Batch rename response with all results.
    """
    return rename_service.batch_rename(request)
