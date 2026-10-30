"""Session management service - database-backed."""

import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.infrastructure.repositories.session_repository import SessionRepository
from server.models.auth import EXPIRE_HOURS_MAP, ExpireOption, LoginHistoryItem, SessionInfo

logger = logging.getLogger(__name__)

# refresh token 轮换宽限（秒）：多标签页同时刷新时，旧 token 在该窗口内仍可用，
# 否则先完成刷新的那个标签页会把其它标签页踢下线
REFRESH_GRACE_SECONDS = 60


# 浏览器识别顺序表：(UA 中的特征串, 展示名)
# 必须按「专属串优先」排列：Chromium 系浏览器的 UA 里内嵌 "Chrome/..."，
# 若先匹配 chrome，则 Edge / Opera / Samsung 等一律被误判为 Chrome。
# 曾因此把 Edge 153 显示成「Windows - Chrome」。
_BROWSER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("edg/", "Edge"),
    ("edgios", "Edge"),
    ("edga/", "Edge"),
    ("opr/", "Opera"),
    ("opera", "Opera"),
    ("samsungbrowser", "Samsung Internet"),
    ("brave", "Brave"),
    ("vivaldi", "Vivaldi"),
    ("yabrowser", "Yandex"),
    ("micromessenger", "微信"),
    ("qqbrowser", "QQ 浏览器"),
    ("ucbrowser", "UC 浏览器"),
    ("fxios", "Firefox"),
    ("firefox/", "Firefox"),
    ("crios", "Chrome"),
    ("chrome/", "Chrome"),
    ("safari/", "Safari"),
)

# 操作系统识别顺序表：同理，需要先匹配更具体的串。
# Windows 的 UA 一定含 "windows"；iPhone/iPad 的 UA 也含 "mac os x"，故 iOS 必须先判。
_OS_PATTERNS: tuple[tuple[str, str], ...] = (
    ("windows", "Windows"),
    ("iphone", "iOS"),
    ("ipad", "iPadOS"),
    ("cros", "ChromeOS"),
    ("android", "Android"),
    ("mac os x", "macOS"),
    ("linux", "Linux"),
)


def _match(patterns: tuple[tuple[str, str], ...], ua_lower: str) -> str | None:
    """按顺序返回第一个命中的展示名。"""
    for token, name in patterns:
        if token in ua_lower:
            return name
    return None


def _parse_user_agent(user_agent: str | None) -> str:
    """Parse user agent to determine device type."""
    if not user_agent:
        return "desktop"
    ua_lower = user_agent.lower()
    # iPad 与 Android 平板含 "mobile" 的情况不少（如 iPadOS 桌面级 UA），
    # 故平板特征先判，避免把平板显示成手机
    if "ipad" in ua_lower or "tablet" in ua_lower:
        return "tablet"
    if "mobile" in ua_lower or "android" in ua_lower or "iphone" in ua_lower:
        return "mobile"
    return "desktop"


def _generate_device_name(user_agent: str | None, ip_address: str | None) -> str:
    """Generate a device name from user agent."""
    if not user_agent:
        return f"Unknown Device ({ip_address or 'unknown'})"
    ua_lower = user_agent.lower()
    os_name = _match(_OS_PATTERNS, ua_lower) or "Unknown OS"
    browser = _match(_BROWSER_PATTERNS, ua_lower) or "Browser"
    return f"{os_name} - {browser}"


class SessionService:
    """Service for managing user sessions with database connection pool."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._repo = SessionRepository(db_path)

    async def _get_max_sessions(self) -> int:
        """Get max sessions from config."""
        from server.domain.identity.auth_config_service import get_auth_config_service_async
        service = await get_auth_config_service_async()
        config = await service.get_auth_config()
        return config.max_sessions

    async def create_session(
        self,
        user_id: int,
        expire_option: ExpireOption,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_name: str | None = None,
    ) -> tuple[str, str]:
        """
        Create a new session.

        Returns:
            Tuple of (session_id, refresh_token)
        """
        session_id = str(uuid.uuid4())
        refresh_token = secrets.token_urlsafe(32)
        refresh_token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()

        expire_hours = EXPIRE_HOURS_MAP.get(expire_option, 24 * 7)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expire_hours)

        device_type = _parse_user_agent(user_agent)
        if not device_name:
            device_name = _generate_device_name(user_agent, ip_address)

        # Cleanup excess sessions
        await self._cleanup_excess_sessions(user_id)

        await self._repo.insert_session(
            session_id=session_id,
            user_id=user_id,
            refresh_token_hash=refresh_token_hash,
            device_name=device_name,
            device_type=device_type,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at.isoformat(),
        )

        logger.info(f"Session created: {session_id[:8]}... for user {user_id}")
        return session_id, refresh_token

    async def _cleanup_excess_sessions(self, user_id: int) -> None:
        """Remove oldest sessions if exceeding max limit."""
        max_sessions = await self._get_max_sessions()

        count = await self._repo.count_sessions(user_id)

        if count >= max_sessions:
            await self._repo.delete_oldest_sessions(user_id, count - max_sessions + 1)
            logger.info(f"Cleaned up {count - max_sessions + 1} old sessions for user {user_id}")

    async def verify_refresh_token(self, refresh_token: str) -> tuple[str | None, int | None]:
        """
        Verify refresh token and return session_id and user_id if valid.

        Returns:
            Tuple of (session_id, user_id) or (None, None) if invalid
        """
        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()

        # Debug query
        debug_row = await self._repo.find_by_token(token_hash)
        if debug_row:
            logger.debug(f"Found session: id={debug_row[0]}, expires_at={debug_row[2]}")
            if debug_row[2] <= now:
                logger.warning(f"Session expired: expires_at={debug_row[2]} <= now={now}")
        else:
            logger.debug("No matching session found (token hash mismatch)")

        # Actual verification
        row = await self._repo.find_valid_by_token(token_hash, now)
        if not row:
            return None, None

        # Update last used time
        await self._repo.touch_session(row[0], now)

        logger.debug(f"Refresh token verified: session_id={row[0]}, user_id={row[1]}")
        return row[0], row[1]

    async def rotate_refresh_token(
        self,
        session_id: str,
        grace_seconds: int = REFRESH_GRACE_SECONDS,
    ) -> str | None:
        """轮换 refresh token：签发新值，旧值保留 grace_seconds 的宽限。

        轮换的意义：refresh token 有效期 7 天且可反复续期，一旦泄露等于长期通行证；
        每次刷新都换新的，配合宽限期又不会误伤并发刷新的其它标签页。

        Returns:
            新的 refresh token；会话已不存在时返回 None（调用方应要求重新登录）。
        """
        new_token = secrets.token_urlsafe(32)
        new_hash = hashlib.sha256(new_token.encode()).hexdigest()
        now = datetime.now(timezone.utc)
        prev_valid_until = (now + timedelta(seconds=grace_seconds)).isoformat()

        rotated = await self._repo.rotate_token_hash(
            session_id=session_id,
            new_hash=new_hash,
            prev_valid_until=prev_valid_until,
            now=now.isoformat(),
        )
        if not rotated:
            logger.warning(f"Refresh token 轮换失败（会话不存在）: {session_id[:8]}...")
            return None

        logger.debug(f"Refresh token rotated: session_id={session_id[:8]}...")
        return new_token

    async def revoke_session(self, session_id: str) -> bool:
        """Revoke a session by ID."""
        deleted = await self._repo.delete_session(session_id)
        if deleted:
            logger.info(f"Session revoked: {session_id[:8]}...")
        return deleted

    async def revoke_all_sessions(self, user_id: int, except_session_id: str | None = None) -> int:
        """Revoke all sessions for a user, optionally except one."""
        count = await self._repo.delete_user_sessions(user_id, except_session_id)
        logger.info(f"Revoked {count} sessions for user {user_id}")
        return count

    async def get_sessions(self, user_id: int, current_session_id: str | None = None) -> list[SessionInfo]:
        """Get all active sessions for a user."""
        now = datetime.now(timezone.utc).isoformat()

        # Clean up expired sessions
        await self._repo.delete_expired(now)

        rows = await self._repo.list_user_sessions(user_id)

        sessions = []
        for row in rows:
            # 读取时用 UA 原文重新推导：库里存的是登录那一刻算出的字符串，
            # 解析规则改进后旧记录不会自动更新；UA 是原样保存的，重新推导
            # 既能修正历史行，又不必回写数据库。
            user_agent = row[7]
            sessions.append(
                SessionInfo(
                    id=row[0],
                    device_name=(
                        _generate_device_name(user_agent, row[3]) if user_agent else (row[1] or "Unknown")
                    ),
                    device_type=(_parse_user_agent(user_agent) if user_agent else (row[2] or "desktop")),
                    ip_address=row[3] or "unknown",
                    created_at=datetime.fromisoformat(row[4]),
                    last_used_at=datetime.fromisoformat(row[5]),
                    is_current=row[0] == current_session_id,
                    expires_at=datetime.fromisoformat(row[6]),
                )
            )
        return sessions

    async def record_login(
        self,
        username: str,
        success: bool,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_name: str | None = None,
        failure_reason: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Record a login attempt in history."""
        if not device_name and user_agent:
            device_name = _generate_device_name(user_agent, ip_address)

        await self._repo.insert_login_history(
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            device_name=device_name,
            success=1 if success else 0,
            failure_reason=failure_reason,
            session_id=session_id,
        )

        log_msg = f"Login {'success' if success else 'failed'}: {username} from {ip_address}"
        if failure_reason:
            log_msg += f" ({failure_reason})"
        logger.info(log_msg)

    async def get_login_history(
        self, username: str, limit: int = 20, offset: int = 0
    ) -> tuple[list[LoginHistoryItem], int]:
        """Get login history for a user."""
        # Get total count
        total = await self._repo.count_login_history(username)

        # Get records
        rows = await self._repo.list_login_history(username, limit, offset)

        items = []
        for row in rows:
            items.append(
                LoginHistoryItem(
                    id=row[0],
                    ip_address=row[1] or "unknown",
                    # 查询结果里是 (user_agent, device_name) 顺序，原实现把两者取反了：
                    # device_name 拿到的是 UA 原文，user_agent 拿到的是设备名。
                    # 这里按 UA 重新推导，历史记录也能显示正确的浏览器。
                    device_name=(
                        _generate_device_name(row[2], row[1]) if row[2] else row[3]
                    ),
                    user_agent=row[2],
                    login_time=datetime.fromisoformat(row[4]),
                    success=bool(row[5]),
                    failure_reason=row[6],
                )
            )
        return items, total

    async def cleanup_old_history(self, days: int = 90) -> int:
        """Clean up login history older than specified days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)

        count = await self._repo.delete_old_history(cutoff.isoformat())
        if count > 0:
            logger.info(f"Cleaned up {count} old login history records")
        return count


# Singleton instance
session_service = SessionService()
