"""Emby configuration API routes."""

from fastapi import APIRouter, Depends

from server.api.deps import get_emby_service, require_auth
from server.domain.integration.emby_service import EmbyService
from server.models.emby import (
    ConflictCheckRequest,
    ConflictCheckResult,
    EmbyConfig,
    EmbyConfigRequest,
    EmbyConfigResponse,
    EmbyStatus,
    EmbyTestResponse,
)

router = APIRouter(prefix="/api/emby", tags=["emby"], dependencies=[Depends(require_auth)])


def _to_response(config: EmbyConfig) -> EmbyConfigResponse:
    """EmbyConfig -> 响应模型。"""
    return EmbyConfigResponse(
        enabled=config.enabled,
        server_url=config.server_url,
        has_api_key=bool(config.api_key),
        user_id=config.user_id,
        library_ids=config.library_ids,
        check_before_scrape=config.check_before_scrape,
        timeout=config.timeout,
    )


@router.get("/config", response_model=EmbyConfigResponse)
async def get_emby_config(
    service: EmbyService = Depends(get_emby_service),
) -> EmbyConfigResponse:
    """获取 Emby 配置"""
    config = await service.get_config()
    return _to_response(config)


@router.put("/config", response_model=EmbyConfigResponse)
async def save_emby_config(
    request: EmbyConfigRequest,
    service: EmbyService = Depends(get_emby_service),
) -> EmbyConfigResponse:
    """保存 Emby 配置"""
    config = await service.build_config_from_request(request)
    await service.save_config(config)
    return _to_response(config)


@router.get("/status", response_model=EmbyStatus)
async def get_emby_status(
    service: EmbyService = Depends(get_emby_service),
) -> EmbyStatus:
    """获取 Emby 连接状态"""
    return await service.get_status()


@router.post("/test", response_model=EmbyTestResponse)
async def test_emby_connection(
    request: EmbyConfigRequest | None = None,
    service: EmbyService = Depends(get_emby_service),
) -> EmbyTestResponse:
    """测试 Emby 连接，可传入配置或使用已保存的配置"""
    if request:
        config = await service.build_test_config(request)
        return await service.test_connection_with_config(config)
    return await service.test_connection()


@router.post("/check-conflict", response_model=ConflictCheckResult)
async def check_conflict(
    request: ConflictCheckRequest,
    service: EmbyService = Depends(get_emby_service),
) -> ConflictCheckResult:
    """检查 Emby 冲突"""
    return await service.check_conflict(request)
