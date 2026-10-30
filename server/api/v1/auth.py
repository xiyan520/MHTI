"""Authentication API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request

from server.api.deps import AuthContext, get_client_ip, require_auth
from server.domain.identity.auth_service import auth_service
from server.domain.identity.session_service import session_service
from server.models.auth import (
    AuthStatusResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    LoginHistoryResponse,
    LoginRequest,
    RefreshRequest,
    RefreshResponse,
    RegisterRequest,
    SessionListResponse,
    TokenResponse,
    UpdateAvatarRequest,
    UpdateAvatarResponse,
    UpdateUsernameRequest,
    UpdateUsernameResponse,
    UserProfileResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatusResponse)
async def get_status() -> AuthStatusResponse:
    """Check if admin account is initialized."""
    initialized = await auth_service.is_initialized()
    return AuthStatusResponse(initialized=initialized)


@router.post("/register", response_model=TokenResponse)
async def register(request: Request, data: RegisterRequest) -> TokenResponse:
    """Register admin account (only works if not initialized)."""
    if await auth_service.is_initialized():
        raise HTTPException(status_code=400, detail="管理员账号已存在")

    success = await auth_service.register_admin(data.username, data.password)
    if not success:
        raise HTTPException(status_code=400, detail="注册失败，请重试")

    # 获取用户ID并创建会话
    user_id = await auth_service.get_user_id(data.username)
    if not user_id:
        raise HTTPException(status_code=500, detail="创建用户失败")

    client_ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    session_id, refresh_token = await session_service.create_session(
        user_id=user_id,
        expire_option="7d",
        ip_address=client_ip,
        user_agent=user_agent,
    )

    access_token, expires_in = auth_service.create_access_token(data.username, session_id)
    refresh_expires_in = auth_service.get_refresh_expire_seconds("7d")

    # 记录登录历史
    await session_service.record_login(
        username=data.username,
        success=True,
        ip_address=client_ip,
        user_agent=user_agent,
        session_id=session_id,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        session_id=session_id,
    )


@router.post("/login", response_model=TokenResponse)
async def login(request: Request, data: LoginRequest) -> TokenResponse:
    """Login and get access token."""
    client_ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    if not await auth_service.is_initialized():
        raise HTTPException(status_code=400, detail="请先注册管理员账号")

    # Check if locked
    is_locked, remaining = await auth_service.is_locked(client_ip)
    if is_locked:
        raise HTTPException(
            status_code=429,
            detail=f"账号已锁定，请 {remaining} 分钟后重试",
        )

    # Verify credentials
    if not await auth_service.verify_credentials(data.username, data.password):
        remaining_attempts = await auth_service.record_failed_attempt(client_ip)

        # 记录失败的登录
        await session_service.record_login(
            username=data.username,
            success=False,
            ip_address=client_ip,
            user_agent=user_agent,
            failure_reason="用户名或密码错误",
        )

        if remaining_attempts == 0:
            raise HTTPException(
                status_code=429,
                detail="登录失败次数过多，账号已锁定",
            )
        raise HTTPException(
            status_code=401,
            detail=f"用户名或密码错误，剩余 {remaining_attempts} 次尝试",
        )

    # Clear failed attempts
    await auth_service.clear_failed_attempts(client_ip)

    # Get user ID and create session
    user_id = await auth_service.get_user_id(data.username)
    if not user_id:
        raise HTTPException(status_code=500, detail="用户不存在")

    session_id, refresh_token = await session_service.create_session(
        user_id=user_id,
        expire_option=data.expire_option,
        ip_address=client_ip,
        user_agent=user_agent,
        device_name=data.device_name,
    )

    access_token, expires_in = auth_service.create_access_token(data.username, session_id)
    refresh_expires_in = auth_service.get_refresh_expire_seconds(data.expire_option)

    # 记录成功的登录
    await session_service.record_login(
        username=data.username,
        success=True,
        ip_address=client_ip,
        user_agent=user_agent,
        device_name=data.device_name,
        session_id=session_id,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        session_id=session_id,
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_token(data: RefreshRequest) -> RefreshResponse:
    """Refresh access token using refresh token."""
    session_id, user_id = await session_service.verify_refresh_token(data.refresh_token)

    if not session_id or not user_id:
        raise HTTPException(status_code=401, detail="Refresh Token 无效或已过期")

    username = await auth_service.get_username_by_id(user_id)
    if not username:
        raise HTTPException(status_code=401, detail="用户不存在")

    access_token, expires_in = auth_service.create_access_token(username, session_id)

    # 每次刷新都轮换 refresh token：泄露的旧值最多再用到宽限期结束
    new_refresh_token = await session_service.rotate_refresh_token(session_id)

    return RefreshResponse(
        access_token=access_token,
        expires_in=expires_in,
        refresh_token=new_refresh_token or data.refresh_token,
    )


@router.post("/logout")
async def logout(auth: AuthContext = Depends(require_auth)) -> dict:
    """Logout and revoke current session."""
    await session_service.revoke_session(auth.session_id)
    return {"message": "已登出"}


@router.get("/verify")
async def verify(auth: AuthContext = Depends(require_auth)) -> dict:
    """Verify access token."""
    return {"valid": True, "username": auth.username, "session_id": auth.session_id}


@router.get("/sessions", response_model=SessionListResponse)
async def get_sessions(auth: AuthContext = Depends(require_auth)) -> SessionListResponse:
    """Get all active sessions for current user."""
    user_id = await auth_service.get_user_id(auth.username)
    if not user_id:
        raise HTTPException(status_code=401, detail="用户不存在")

    sessions = await session_service.get_sessions(user_id, auth.session_id)
    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, auth: AuthContext = Depends(require_auth)) -> dict:
    """Revoke a specific session."""
    if session_id == auth.session_id:
        raise HTTPException(status_code=400, detail="不能注销当前会话，请使用登出接口")

    success = await session_service.revoke_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")

    return {"message": "会话已注销"}


@router.delete("/sessions")
async def revoke_all_sessions(auth: AuthContext = Depends(require_auth)) -> dict:
    """Revoke all sessions except current one."""
    user_id = await auth_service.get_user_id(auth.username)
    if not user_id:
        raise HTTPException(status_code=401, detail="用户不存在")

    count = await session_service.revoke_all_sessions(user_id, except_session_id=auth.session_id)
    return {"message": f"已注销 {count} 个其他会话"}


@router.get("/history", response_model=LoginHistoryResponse)
async def get_login_history(
    limit: int = 20,
    offset: int = 0,
    auth: AuthContext = Depends(require_auth),
) -> LoginHistoryResponse:
    """Get login history for current user."""
    items, total = await session_service.get_login_history(auth.username, limit, offset)
    return LoginHistoryResponse(items=items, total=total)


# ========== 账户管理端点 ==========


@router.get("/profile", response_model=UserProfileResponse)
async def get_profile(auth: AuthContext = Depends(require_auth)) -> UserProfileResponse:
    """Get current user profile."""
    profile = await auth_service.get_user_profile(auth.username)
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")

    return UserProfileResponse(
        username=profile["username"],
        avatar=profile["avatar"],
        created_at=profile["created_at"],
    )


@router.put("/password", response_model=ChangePasswordResponse)
async def change_password(
    data: ChangePasswordRequest,
    auth: AuthContext = Depends(require_auth),
) -> ChangePasswordResponse:
    """Change current user's password."""
    success = await auth_service.change_password(
        auth.username,
        data.current_password,
        data.new_password,
    )

    if not success:
        return ChangePasswordResponse(success=False, message="当前密码错误")

    return ChangePasswordResponse(success=True, message="密码修改成功")


@router.put("/username", response_model=UpdateUsernameResponse)
async def update_username(
    data: UpdateUsernameRequest,
    auth: AuthContext = Depends(require_auth),
) -> UpdateUsernameResponse:
    """Update current user's username."""
    success, message = await auth_service.update_username(
        auth.username,
        data.new_username,
        data.password,
    )

    if not success:
        return UpdateUsernameResponse(success=False, message=message)

    return UpdateUsernameResponse(
        success=True,
        message=message,
        new_username=data.new_username,
    )


@router.put("/avatar", response_model=UpdateAvatarResponse)
async def update_avatar(
    data: UpdateAvatarRequest,
    auth: AuthContext = Depends(require_auth),
) -> UpdateAvatarResponse:
    """Update current user's avatar."""
    success, message = await auth_service.update_avatar(auth.username, data.avatar)

    if not success:
        return UpdateAvatarResponse(success=False, message=message)

    return UpdateAvatarResponse(success=True, message=message, avatar=data.avatar)


@router.delete("/avatar")
async def delete_avatar(auth: AuthContext = Depends(require_auth)) -> dict:
    """Delete current user's avatar."""
    await auth_service.delete_avatar(auth.username)
    return {"success": True, "message": "头像已删除"}
