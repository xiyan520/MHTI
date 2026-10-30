"""Filename parsing API routes."""

from fastapi import APIRouter, Depends

from server.api.deps import get_parser_service, require_auth
from server.domain.parsing.parser_service import ParserService
from server.models.parser import (
    BatchParseRequest,
    BatchParseResponse,
    ParseRequest,
    ParseResponse,
)

router = APIRouter(prefix="/api", tags=["parser"], dependencies=[Depends(require_auth)])


@router.post("/parse", response_model=ParseResponse)
async def parse_filename(
    request: ParseRequest,
    parser_service: ParserService = Depends(get_parser_service),
) -> ParseResponse:
    """
    Parse episode information from a filename.

    Args:
        request: ParseRequest containing filename and optional filepath.
        parser_service: Injected ParserService instance.

    Returns:
        ParseResponse with parsed episode information.
    """
    result = parser_service.parse(request.filename, request.filepath)
    return ParseResponse(result=result)


@router.post("/parse/batch", response_model=BatchParseResponse)
async def parse_batch(
    request: BatchParseRequest,
    parser_service: ParserService = Depends(get_parser_service),
) -> BatchParseResponse:
    """
    Parse episode information from multiple filenames.

    Args:
        request: BatchParseRequest containing list of files.
        parser_service: Injected ParserService instance.

    Returns:
        BatchParseResponse with parsed results and success rate.
    """
    files = [(f.filename, f.filepath) for f in request.files]
    results, success_rate = parser_service.parse_batch(files)
    return BatchParseResponse(results=results, success_rate=success_rate)
