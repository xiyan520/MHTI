"""Middleware components for the FastAPI application."""

import logging
import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from server.common.exceptions import AppException, ErrorCode
from server.common.http import get_client_ip

logger = logging.getLogger(__name__)


def setup_exception_handlers(app: FastAPI) -> None:
    """
    Setup global exception handlers for the application.

    Args:
        app: FastAPI application instance
    """

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
        """Handle application exceptions."""
        logger.warning(
            f"AppException: {exc.code.value} - {exc.message}",
            extra={
                "error_code": exc.code.value,
                "path": request.url.path,
                "method": request.method,
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Handle unexpected exceptions."""
        logger.exception(
            f"Unhandled exception: {type(exc).__name__}: {exc}",
            extra={
                "path": request.url.path,
                "method": request.method,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "服务器内部错误",
                    "details": {},
                }
            },
        )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware for logging HTTP requests and responses.

    Logs request method, path, status code, and response time.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and log details."""
        start_time = time.perf_counter()

        # Skip logging for health checks and static files
        path = request.url.path
        if path in ("/health", "/favicon.ico") or path.startswith("/static"):
            return await call_next(request)

        # Process request
        response = await call_next(request)

        # Calculate response time
        process_time = (time.perf_counter() - start_time) * 1000

        # Log request details
        logger.info(
            f"{request.method} {path} - {response.status_code} ({process_time:.1f}ms)",
            extra={
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "response_time_ms": round(process_time, 1),
                "client_ip": get_client_ip(request),
            },
        )

        # Add timing header
        response.headers["X-Response-Time"] = f"{process_time:.1f}ms"

        return response


class CORSDebugMiddleware(BaseHTTPMiddleware):
    """
    Debug middleware for CORS issues.

    Only use in development mode.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log CORS-related headers for debugging."""
        origin = request.headers.get("Origin")
        if origin and request.method == "OPTIONS":
            logger.debug(
                f"CORS preflight: {origin} -> {request.url.path}",
                extra={
                    "origin": origin,
                    "method": request.method,
                    "path": request.url.path,
                },
            )

        return await call_next(request)


def setup_middleware(app: FastAPI, debug: bool = False) -> None:
    """
    Setup all middleware for the application.

    Args:
        app: FastAPI application instance
        debug: Enable debug middleware
    """
    # Request logging (add first so it's executed last)
    app.add_middleware(RequestLoggingMiddleware)

    # Debug middleware
    if debug:
        app.add_middleware(CORSDebugMiddleware)

    logger.info("Middleware configured")
