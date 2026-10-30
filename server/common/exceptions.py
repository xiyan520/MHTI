"""应用异常体系 - 共享内核，被各层引用。"""

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Application error codes."""

    # General errors (1xxx)
    INTERNAL_ERROR = "E1000"
    VALIDATION_ERROR = "E1001"
    NOT_FOUND = "E1002"
    PERMISSION_DENIED = "E1003"

    # Authentication errors (2xxx)
    AUTH_REQUIRED = "E2000"
    AUTH_INVALID_TOKEN = "E2001"
    AUTH_EXPIRED_TOKEN = "E2002"
    AUTH_INVALID_CREDENTIALS = "E2003"
    AUTH_ACCOUNT_LOCKED = "E2004"
    AUTH_SESSION_EXPIRED = "E2005"

    # File system errors (3xxx)
    FILE_NOT_FOUND = "E3000"
    FOLDER_NOT_FOUND = "E3001"
    INVALID_PATH = "E3002"
    FILE_EXISTS = "E3003"
    FILE_OPERATION_FAILED = "E3004"

    # TMDB errors (4xxx)
    TMDB_NOT_CONFIGURED = "E4000"
    TMDB_INVALID_CREDENTIALS = "E4001"
    TMDB_REQUEST_FAILED = "E4002"
    TMDB_NOT_FOUND = "E4003"
    TMDB_RATE_LIMITED = "E4004"

    # Scraper errors (5xxx)
    SCRAPE_PARSE_FAILED = "E5000"
    SCRAPE_SEARCH_FAILED = "E5001"
    SCRAPE_NO_MATCH = "E5002"
    SCRAPE_MOVE_FAILED = "E5003"
    SCRAPE_NFO_FAILED = "E5004"
    SCRAPE_CONFLICT = "E5005"

    # Configuration errors (6xxx)
    CONFIG_NOT_FOUND = "E6000"
    CONFIG_INVALID = "E6001"

    # External service errors (7xxx)
    EMBY_NOT_CONFIGURED = "E7000"
    EMBY_CONNECTION_FAILED = "E7001"
    EMBY_CONFLICT = "E7002"


class AppException(Exception):
    """
    Base exception for all application errors.

    Attributes:
        message: Human-readable error message.
        code: Error code for programmatic handling.
        details: Additional error details.
        status_code: HTTP status code for API responses.
    """

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        details: dict[str, Any] | None = None,
        status_code: int = 500,
    ) -> None:
        self.message = message
        self.code = code
        self.details = details or {}
        self.status_code = status_code
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        """Convert exception to dictionary for API response."""
        return {
            "error": {
                "code": self.code.value,
                "message": self.message,
                "details": self.details,
            }
        }


# ============================================================
# Authentication Exceptions
# ============================================================


class AuthenticationError(AppException):
    """Base class for authentication errors."""

    def __init__(
        self,
        message: str = "认证失败",
        code: ErrorCode = ErrorCode.AUTH_REQUIRED,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, details, status_code=401)


class InvalidTokenError(AuthenticationError):
    """Raised when token is invalid."""

    def __init__(self, message: str = "Token 无效") -> None:
        super().__init__(message, ErrorCode.AUTH_INVALID_TOKEN)


class ExpiredTokenError(AuthenticationError):
    """Raised when token has expired."""

    def __init__(self, message: str = "Token 已过期") -> None:
        super().__init__(message, ErrorCode.AUTH_EXPIRED_TOKEN)


class InvalidCredentialsError(AuthenticationError):
    """Raised when credentials are invalid."""

    def __init__(self, message: str = "用户名或密码错误") -> None:
        super().__init__(message, ErrorCode.AUTH_INVALID_CREDENTIALS)


class AccountLockedError(AuthenticationError):
    """Raised when account is locked due to too many failed attempts."""

    def __init__(self, minutes: int = 15) -> None:
        super().__init__(
            f"账户已锁定，请 {minutes} 分钟后重试",
            ErrorCode.AUTH_ACCOUNT_LOCKED,
            {"lockout_minutes": minutes},
        )


# ============================================================
# File System Exceptions
# ============================================================


class FileSystemError(AppException):
    """Base class for file system errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.FILE_OPERATION_FAILED,
        path: str | None = None,
    ) -> None:
        details = {"path": path} if path else {}
        super().__init__(message, code, details, status_code=400)


class FileNotFoundError(FileSystemError):
    """Raised when file does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"文件不存在: {path}", ErrorCode.FILE_NOT_FOUND, path)


class FolderNotFoundError(FileSystemError):
    """Raised when folder does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"文件夹不存在: {path}", ErrorCode.FOLDER_NOT_FOUND, path)


class InvalidPathError(FileSystemError):
    """Raised when path is invalid."""

    def __init__(self, path: str, reason: str = "无效路径") -> None:
        super().__init__(f"{reason}: {path}", ErrorCode.INVALID_PATH, path)


class FileExistsError(FileSystemError):
    """Raised when file already exists."""

    def __init__(self, path: str) -> None:
        super().__init__(f"文件已存在: {path}", ErrorCode.FILE_EXISTS, path)


class InvalidFolderError(FileSystemError):
    """Raised when folder path is invalid."""

    def __init__(self, path: str, reason: str = "无效文件夹路径") -> None:
        super().__init__(f"{reason}: {path}", ErrorCode.INVALID_PATH, path)


class PermissionDeniedError(FileSystemError):
    """Raised when permission is denied for file operation."""

    def __init__(self, path: str, operation: str = "访问") -> None:
        super().__init__(
            f"权限被拒绝 ({operation}): {path}",
            ErrorCode.PERMISSION_DENIED,
            path,
        )
        self.status_code = 403


# ============================================================
# TMDB Exceptions
# ============================================================


class TMDBError(AppException):
    """Base class for TMDB errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.TMDB_REQUEST_FAILED,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code, details, status_code=502)


class TMDBNotConfiguredError(TMDBError):
    """Raised when TMDB is not configured."""

    def __init__(self, missing: str = "Cookie/API Token") -> None:
        super().__init__(
            f"请先配置 TMDB {missing}",
            ErrorCode.TMDB_NOT_CONFIGURED,
            {"missing": missing},
        )


class TMDBInvalidCredentialsError(TMDBError):
    """Raised when TMDB credentials are invalid."""

    def __init__(self, credential_type: str = "Cookie") -> None:
        super().__init__(
            f"TMDB {credential_type} 已失效，请更新",
            ErrorCode.TMDB_INVALID_CREDENTIALS,
            {"credential_type": credential_type},
        )


class TMDBNotFoundError(TMDBError):
    """Raised when TMDB resource is not found."""

    def __init__(self, resource_type: str = "剧集", resource_id: int | str | None = None) -> None:
        details: dict[str, Any] = {"resource_type": resource_type}
        if resource_id:
            details["resource_id"] = resource_id
        super().__init__(
            f"TMDB {resource_type}未找到",
            ErrorCode.TMDB_NOT_FOUND,
            details,
        )
        self.status_code = 404


class TMDBTimeoutError(TMDBError):
    """Raised when TMDB request times out."""

    def __init__(self, endpoint: str | None = None) -> None:
        details = {"endpoint": endpoint} if endpoint else {}
        super().__init__(
            "TMDB 请求超时",
            ErrorCode.TMDB_REQUEST_FAILED,
            details,
        )
        self.status_code = 408


class TMDBConnectionError(TMDBError):
    """Raised when TMDB connection fails."""

    def __init__(self, reason: str | None = None) -> None:
        details = {"reason": reason} if reason else {}
        super().__init__(
            f"TMDB 连接失败: {reason}" if reason else "TMDB 连接失败",
            ErrorCode.TMDB_REQUEST_FAILED,
            details,
        )
        self.status_code = 502


# ============================================================
# Scraper Exceptions
# ============================================================


class ScrapeError(AppException):
    """Base class for scraper errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.SCRAPE_PARSE_FAILED,
        file_path: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        full_details = details or {}
        if file_path:
            full_details["file_path"] = file_path
        super().__init__(message, code, full_details, status_code=400)


class ParseError(ScrapeError):
    """Raised when filename parsing fails."""

    def __init__(self, file_path: str, reason: str = "无法解析文件名") -> None:
        super().__init__(reason, ErrorCode.SCRAPE_PARSE_FAILED, file_path)


class NoMatchError(ScrapeError):
    """Raised when no TMDB match is found."""

    def __init__(self, query: str, file_path: str | None = None) -> None:
        super().__init__(
            f"未找到匹配的剧集: {query}",
            ErrorCode.SCRAPE_NO_MATCH,
            file_path,
            {"query": query},
        )


class ConflictError(ScrapeError):
    """Raised when there's a file or Emby conflict."""

    def __init__(
        self,
        message: str,
        conflict_type: str = "file",
        file_path: str | None = None,
    ) -> None:
        super().__init__(
            message,
            ErrorCode.SCRAPE_CONFLICT,
            file_path,
            {"conflict_type": conflict_type},
        )


# ============================================================
# Configuration Exceptions
# ============================================================


class ConfigurationError(AppException):
    """Base class for configuration errors."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.CONFIG_INVALID,
        config_key: str | None = None,
    ) -> None:
        details = {"config_key": config_key} if config_key else {}
        super().__init__(message, code, details, status_code=400)


class ConfigNotFoundError(ConfigurationError):
    """Raised when configuration is not found."""

    def __init__(self, config_key: str) -> None:
        super().__init__(
            f"配置项不存在: {config_key}",
            ErrorCode.CONFIG_NOT_FOUND,
            config_key,
        )


# ============================================================
# Utility Functions
# ============================================================


def not_found(resource: str, identifier: Any = None) -> AppException:
    """Create a not found exception."""
    msg = f"{resource}不存在"
    if identifier:
        msg = f"{resource}不存在: {identifier}"
    return AppException(msg, ErrorCode.NOT_FOUND, status_code=404)


def validation_error(message: str, field: str | None = None) -> AppException:
    """Create a validation error exception."""
    details = {"field": field} if field else {}
    return AppException(message, ErrorCode.VALIDATION_ERROR, details, status_code=422)
