"""FastAPI application entry point."""

import logging
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.infrastructure.config import DATA_DIR

# 日志目录（跟随 DATA_DIR，与 SQLite/密钥落在同一挂载点）
LOG_DIR = DATA_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# 日志格式
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
)
logger = logging.getLogger(__name__)


def setup_file_logging() -> RotatingFileHandler | None:
    """设置文件日志处理器（带轮转）。"""
    try:
        file_handler = RotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logging.getLogger().addHandler(file_handler)
        logger.info(f"File logging enabled: {LOG_DIR / 'app.log'}")
        return file_handler
    except Exception as e:
        logger.warning(f"Failed to setup file logging: {e}")
        return None

# CORS allowed origins (Docker environment)
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3500",
    "http://127.0.0.1:3500",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost",
    "http://127.0.0.1",
]

# 是否放开 CORS。默认关闭：生产由 Caddy 同源托管，只有本地开发才需要跨源。
# 需要局域网访问时用 CORS_ALLOW_ALL=true 显式开启。
CORS_ALLOW_ALL_ORIGINS = os.getenv("CORS_ALLOW_ALL", "false").lower() == "true"

# 允许来源：本机 + 私网段（192.168.x.x / 10.x.x.x / 172.16-31.x.x）
CORS_ALLOW_ALL_PATTERN = (
    r"https?://(?:localhost|127\.0\.0\.1|\[::1\]|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?::\d+)?"
)

# Allow additional origins from environment
if extra_origins := os.getenv("CORS_ORIGINS"):
    CORS_ORIGINS.extend(extra_origins.split(","))

# API Routers
from server.api.middleware import setup_exception_handlers, setup_middleware
from server.api.v1.auth import router as auth_router
from server.api.v1.config import router as config_router
from server.api.v1.emby import router as emby_router
from server.api.v1.files import router as files_router
from server.api.v1.frontend_config import router as frontend_config_router
from server.api.v1.history import router as history_router
from server.api.v1.images import router as images_router
from server.api.v1.logs import router as logs_router
from server.api.v1.manual_job import router as manual_job_router
from server.api.v1.nfo import router as nfo_router
from server.api.v1.parser import router as parser_router
from server.api.v1.rename import router as rename_router
from server.api.v1.scheduler import router as scheduler_router
from server.api.v1.scrape_job import router as scrape_job_router
from server.api.v1.scraper import router as scraper_router
from server.api.v1.subtitles import router as subtitles_router
from server.api.v1.templates import router as templates_router
from server.api.v1.tmdb import router as tmdb_router
from server.api.v1.watcher import router as watcher_router
from server.api.v1.websocket import router as websocket_router

# Core components
from server.bootstrap import cleanup_services, get_watcher_service, init_services
from server.infrastructure.db import close_database, init_database


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup
    logger.info("Starting application...")

    # Setup file logging (with rotation)
    file_handler = setup_file_logging()

    # Initialize database with connection pool
    await init_database()

    # Initialize authentication configuration from database
    from server.domain.identity.auth_config_service import get_auth_config_service_async
    from server.infrastructure.config import get_app_config

    auth_config_service = await get_auth_config_service_async()
    get_app_config().set_auth_config(await auth_config_service.get_auth_config())
    logger.info("Authentication configuration loaded from database")

    # 注入 Token 校验实现（鉴权端口实现在上层，由组合根装配）
    from server.api.deps import set_token_verifier
    from server.domain.identity.auth_service import auth_service

    set_token_verifier(auth_service)

    # Initialize service container
    await init_services()

    # Initialize and start log service
    from server.bootstrap import get_log_service
    log_service = get_log_service()
    await log_service.start()

    # Setup database log handler (仅记录 WARNING 及以上级别，减少性能开销)
    from server.infrastructure.log_handler import DatabaseLogHandler
    db_log_handler = DatabaseLogHandler(log_service, batch_size=50, flush_interval=10.0)
    db_log_handler.setLevel(logging.WARNING)  # 只记录警告和错误
    db_log_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(db_log_handler)
    db_log_handler.start()

    logger.info("Log service started")

    # 恢复上次进程遗留的任务：内存队列随进程消失，DB 里的 pending 不会自己跑起来
    from server.application.manual_job_service import recover_pending_jobs as recover_manual_jobs
    from server.application.scrape_job_service import recover_pending_jobs as recover_scrape_jobs

    try:
        scrape_recovery = await recover_scrape_jobs()
        manual_recovery = await recover_manual_jobs()
        logger.info(f"任务恢复完成: 刮削 {scrape_recovery}, 手动 {manual_recovery}")
    except Exception as e:  # noqa: BLE001 - 恢复是补偿动作，坏数据不该挡住启动
        logger.error(f"任务恢复失败: {e}")

    # Auto-start watcher service if enabled folders exist
    watcher = get_watcher_service()
    folders, _ = await watcher.list_folders()
    if any(f.enabled for f in folders):
        logger.info("Detected enabled watch folders, starting watcher service")
        await watcher.start()

    logger.info("Application started successfully")

    yield

    # Shutdown
    logger.info("Shutting down application...")

    # 取消后台 worker（刮削 + 手动任务），避免它们阻塞在队列上导致退出卡顿
    from server.application.manual_job_service import shutdown_workers as shutdown_manual_workers
    from server.application.scrape_job_service import shutdown_workers as shutdown_scrape_workers
    await shutdown_scrape_workers()
    await shutdown_manual_workers()

    # Stop watcher service
    if watcher._running:
        await watcher.stop()

    # Stop database log handler
    db_log_handler.stop()
    logging.getLogger().removeHandler(db_log_handler)

    # Stop log service (flush remaining logs)
    await log_service.stop()
    logger.info("Log service stopped")

    # Remove file handler
    if file_handler:
        logging.getLogger().removeHandler(file_handler)
        file_handler.close()

    # Cleanup services
    await cleanup_services()

    # Close database connections
    await close_database()

    logger.info("Application shutdown complete")


# Create FastAPI application
app = FastAPI(
    title="MHTI API",
    description="API for scanning and scraping TV series metadata",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# Setup global exception handlers
setup_exception_handlers(app)

# Setup middleware stack
DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"
setup_middleware(app, debug=DEBUG_MODE)

# CORS 配置
# 注意：allow_origins=["*"] 搭配 allow_credentials=True 是非法组合，浏览器会直接
# 拒绝带凭据的响应；放开时改按本机/私网正则放行，既能局域网访问也不会无差别放行
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=CORS_ALLOW_ALL_PATTERN if CORS_ALLOW_ALL_ORIGINS else None,
    allow_origins=[] if CORS_ALLOW_ALL_ORIGINS else CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Response-Time", "X-Request-ID"],
)

# Include API routers (each router defines its own /api/* prefix)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(parser_router)
app.include_router(config_router)
app.include_router(emby_router)
app.include_router(tmdb_router)
app.include_router(nfo_router)
app.include_router(images_router)
app.include_router(templates_router)
app.include_router(rename_router)
app.include_router(subtitles_router)
app.include_router(scheduler_router)
app.include_router(history_router)
app.include_router(watcher_router)
app.include_router(scraper_router)
app.include_router(manual_job_router)
app.include_router(scrape_job_router)
app.include_router(websocket_router)  # WebSocket at /ws
app.include_router(frontend_config_router)  # Frontend runtime config
app.include_router(logs_router)  # Logs management API


@app.get("/health")
async def health_check() -> dict:
    """
    Health check endpoint for Docker/Kubernetes.

    Returns comprehensive health status including:
    - Overall status: healthy, degraded, or unhealthy
    - Database connection status
    - External service configurations
    """
    from server.infrastructure.db import get_db_manager

    health_status = {
        "status": "healthy",
        "checks": {
            "database": "unknown",
            "tmdb_configured": "unknown",
            "emby_configured": "unknown",
        },
    }

    # Check database connection
    try:
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute("SELECT 1")
        health_status["checks"]["database"] = "healthy"
    except Exception as e:
        health_status["checks"]["database"] = f"unhealthy: {str(e)}"
        health_status["status"] = "degraded"

    # Check TMDB configuration (non-blocking)
    try:
        from server.bootstrap import get_config_service
        config_service = get_config_service()
        tmdb_cookie = await config_service.get_tmdb_cookie()
        tmdb_token = await config_service.get_tmdb_api_token()
        if tmdb_cookie and tmdb_token:
            health_status["checks"]["tmdb_configured"] = "configured"
        elif tmdb_cookie or tmdb_token:
            health_status["checks"]["tmdb_configured"] = "partial"
        else:
            health_status["checks"]["tmdb_configured"] = "not_configured"
    except Exception:
        health_status["checks"]["tmdb_configured"] = "check_failed"

    # Check Emby configuration (non-blocking)
    try:
        from server.bootstrap import get_emby_service
        emby_service = get_emby_service()
        emby_config = await emby_service.get_config()
        health_status["checks"]["emby_configured"] = "configured" if emby_config.enabled else "disabled"
    except Exception:
        health_status["checks"]["emby_configured"] = "check_failed"

    return health_status


@app.get("/health/live")
async def liveness_check() -> dict[str, str]:
    """Kubernetes liveness probe - checks if app is running."""
    return {"status": "alive"}


@app.get("/health/ready")
async def readiness_check() -> dict:
    """
    Kubernetes readiness probe - checks if app is ready to serve traffic.

    Returns unhealthy if database is not accessible.
    """
    from server.infrastructure.db import get_db_manager

    try:
        manager = await get_db_manager()
        async with manager.get_connection() as db:
            await db.execute("SELECT 1")
        return {"status": "ready"}
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": str(e)}
        )


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint with API information."""
    return {
        "name": "MHTI API",
        "version": "2.0.0",
        "docs": "/api/docs",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["server"],
    )
