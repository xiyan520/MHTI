"""Authentication models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# 有效期选项类型
ExpireOption = Literal["1h", "1d", "7d", "30d", "never"]

# 有效期映射（小时）
EXPIRE_HOURS_MAP: dict[ExpireOption, int] = {
    "1h": 1,
    "1d": 24,
    "7d": 24 * 7,
    "30d": 24 * 30,
    "never": 24 * 365,  # 1年
}


class LoginRequest(BaseModel):
    """Login request model."""

    username: str
    password: str
    expire_option: ExpireOption = "7d"
    device_name: str | None = None


class RegisterRequest(BaseModel):
    """Register admin request model."""

    username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    """Token response model (双令牌)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token 过期秒数
    refresh_expires_in: int  # refresh token 过期秒数
    session_id: str


class RefreshRequest(BaseModel):
    """Token refresh request."""

    refresh_token: str


class RefreshResponse(BaseModel):
    """Token refresh response."""

    access_token: str
    expires_in: int
    # 轮换后的 refresh token：客户端必须替换本地旧值（旧值仅在宽限期内可用）
    refresh_token: str | None = None


class AuthStatusResponse(BaseModel):
    """Auth initialization status response."""

    initialized: bool


class SessionInfo(BaseModel):
    """Session information."""

    id: str
    device_name: str
    device_type: str
    ip_address: str
    created_at: datetime
    last_used_at: datetime
    is_current: bool
    expires_at: datetime


class SessionListResponse(BaseModel):
    """Session list response."""

    sessions: list[SessionInfo]
    total: int


class LoginHistoryItem(BaseModel):
    """Login history item."""

    id: int
    ip_address: str
    device_name: str | None
    user_agent: str | None
    login_time: datetime
    success: bool
    failure_reason: str | None


class LoginHistoryResponse(BaseModel):
    """Login history response."""

    items: list[LoginHistoryItem]
    total: int


class AuthConfig(BaseModel):
    """
    Authentication configuration.

    Token 有效期说明：
    - Access Token: 固定 15 分钟（access_token_minutes）
    - Refresh Token: 由前端登录选项决定（使用 EXPIRE_HOURS_MAP）
    """

    max_login_attempts: int = 5         # 最大登录尝试次数
    lockout_minutes: int = 15           # 锁定时间（分钟）
    jwt_secret: str = "change-me-in-production"
    access_token_minutes: int = 15      # Access Token 有效期（分钟）
    max_sessions: int = 10              # 最大并发会话数


# ========== 账户管理相关模型 ==========

class ChangePasswordRequest(BaseModel):
    """修改密码请求模型。"""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6, max_length=128)


class ChangePasswordResponse(BaseModel):
    """修改密码响应模型。"""

    success: bool
    message: str


class UpdateUsernameRequest(BaseModel):
    """修改用户名请求模型。"""

    new_username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=1)  # 需要验证密码


class UpdateUsernameResponse(BaseModel):
    """修改用户名响应模型。"""

    success: bool
    message: str
    new_username: str | None = None


class UserProfileResponse(BaseModel):
    """用户资料响应模型。"""

    username: str
    avatar: str | None = None  # Base64 或 URL
    created_at: datetime | None = None


class UpdateAvatarRequest(BaseModel):
    """更新头像请求模型。"""

    avatar: str  # Base64 编码的图片数据


class UpdateAvatarResponse(BaseModel):
    """更新头像响应模型。"""

    success: bool
    message: str
    avatar: str | None = None
