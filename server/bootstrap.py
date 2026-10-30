"""组合根 - 服务注册表与工厂。

容器机制见 ``server.infrastructure.di``；服务名常量见 ``server.common.service_names``。
本模块保留：注册表（_SERVICE_REGISTRY）、统一解析 ``get_service`` 与全部 ``get_*`` 工厂。
"""

import logging
from typing import Any

from server.common.service_names import Services
from server.infrastructure.di import (
    ServiceContainer,
    get_container,
    get_container_async,
)

logger = logging.getLogger(__name__)


async def init_services() -> None:
    """
    Initialize all application services.

    Call this at application startup to register all services.
    """
    container = await get_container_async()

    # Import services here to avoid circular imports
    from server.domain.artifacts.nfo_service import NFOService
    from server.domain.artifacts.rename_service import RenameService
    from server.domain.artifacts.subtitle_service import SubtitleService
    from server.domain.parsing.parser_service import ParserService
    from server.domain.system.config_service import ConfigService
    from server.domain.system.template_service import TemplateService
    from server.infrastructure.realtime import ConnectionManager

    # Register core services (stateless, can be singletons)
    container.register(Services.CONFIG, ConfigService)
    container.register(Services.PARSER, ParserService)
    container.register(Services.NFO, NFOService)
    container.register(Services.RENAME, RenameService)
    container.register(Services.SUBTITLE, SubtitleService)
    container.register(Services.TEMPLATE, TemplateService)

    # Register WebSocket manager as singleton instance
    container.register_instance(Services.WEBSOCKET, ConnectionManager())

    # Services with dependencies (IMAGE, TMDB, EMBY, SCRAPER) will be created lazily
    # via their respective get_*_service() functions
    logger.info("Service container initialized")


async def cleanup_services() -> None:
    """
    Cleanup all services.

    Call this at application shutdown.
    """
    container = await get_container_async()
    container.clear()
    ServiceContainer._instance = None
    logger.info("Service container cleaned up")


# =============================================================================
# Generic Service Factory (DRY principle)
# =============================================================================


# Service registry for automatic resolution
_SERVICE_REGISTRY: dict[str, tuple[str, str]] = {
    Services.CONFIG: ("server.domain.system.config_service", "ConfigService"),
    Services.PARSER: ("server.domain.parsing.parser_service", "ParserService"),
    Services.NFO: ("server.domain.artifacts.nfo_service", "NFOService"),
    Services.RENAME: ("server.domain.artifacts.rename_service", "RenameService"),
    Services.IMAGE: ("server.domain.artifacts.image_service", "ImageService"),
    Services.SUBTITLE: ("server.domain.artifacts.subtitle_service", "SubtitleService"),
    Services.TEMPLATE: ("server.domain.system.template_service", "TemplateService"),
    Services.FILE: ("server.domain.media.file_service", "FileService"),
    Services.HISTORY: ("server.application.history_service", "HistoryService"),
    Services.SCHEDULER: ("server.application.scheduler_service", "SchedulerService"),
    Services.MANUAL_JOB: ("server.application.manual_job_service", "ManualJobService"),
    Services.SCRAPE_JOB: ("server.application.scrape_job_service", "ScrapeJobService"),
    Services.SCRAPED_FILE: ("server.application.scraped_file_service", "ScrapedFileService"),
    Services.WEBSOCKET: ("server.infrastructure.realtime", "ConnectionManager"),
    Services.WATCHER: ("server.application.watcher_service", "WatcherService"),
    Services.LOG: ("server.domain.system.log_service", "LogService"),
}


def get_service(service_name: str) -> Any:
    """
    Unified service resolver - replaces all get_xxx_service functions.

    Args:
        service_name: Service name constant from Services class.

    Returns:
        Service instance (singleton).
    """
    import importlib
    container = get_container()

    if container.has(service_name):
        return container.get(service_name)

    # 依赖型/单例服务走专用工厂（注册表仅覆盖无参可构造的服务）
    if service_name == Services.P115:
        return get_p115_service()
    if service_name == Services.IMAGE:
        return get_image_service()
    if service_name == Services.SCRAPER:
        return get_scraper_service()
    if service_name == Services.TMDB:
        return get_tmdb_service()
    if service_name == Services.EMBY:
        return get_emby_service()
    if service_name == Services.AUTH:
        from server.domain.identity.auth_service import auth_service
        return auth_service
    if service_name == Services.SESSION:
        from server.domain.identity.session_service import session_service
        return session_service

    if service_name not in _SERVICE_REGISTRY:
        raise KeyError(f"Unknown service: {service_name}")

    module_path, class_name = _SERVICE_REGISTRY[service_name]
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    container.register(service_name, cls)
    return container.get(service_name)


def _get_simple_service(service_name: str, service_module: str, service_class: str) -> Any:
    """
    Generic factory for simple services without dependencies.

    Args:
        service_name: Service name constant from Services class.
        service_module: Module path (e.g., 'server.domain.system.config_service').
        service_class: Class name (e.g., 'ConfigService').

    Returns:
        Service instance (singleton).
    """
    import importlib
    container = get_container()
    if not container.has(service_name):
        module = importlib.import_module(service_module)
        cls = getattr(module, service_class)
        container.register(service_name, cls)
    return container.get(service_name)


def _get_singleton_service(service_name: str, service_module: str, service_class: str) -> Any:
    """
    Generic factory for singleton services that should be instantiated immediately.

    Args:
        service_name: Service name constant from Services class.
        service_module: Module path.
        service_class: Class name.

    Returns:
        Service instance (singleton).
    """
    import importlib
    container = get_container()
    if not container.has(service_name):
        module = importlib.import_module(service_module)
        cls = getattr(module, service_class)
        container.register_instance(service_name, cls())
    return container.get(service_name)


# =============================================================================
# FastAPI Dependency Functions - Simple Services
# =============================================================================


def get_config_service():
    """FastAPI dependency for ConfigService."""
    return _get_simple_service(
        Services.CONFIG,
        "server.domain.system.config_service",
        "ConfigService"
    )


def get_parser_service():
    """FastAPI dependency for ParserService."""
    return _get_simple_service(
        Services.PARSER,
        "server.domain.parsing.parser_service",
        "ParserService"
    )


def get_nfo_service():
    """FastAPI dependency for NFOService."""
    return _get_simple_service(
        Services.NFO,
        "server.domain.artifacts.nfo_service",
        "NFOService"
    )


def get_rename_service():
    """FastAPI dependency for RenameService."""
    return _get_simple_service(
        Services.RENAME,
        "server.domain.artifacts.rename_service",
        "RenameService"
    )


def get_image_service():
    """FastAPI dependency for ImageService."""
    from server.domain.artifacts.image_service import ImageService
    container = get_container()
    if not container.has(Services.IMAGE):
        config_service = get_config_service()
        container.register_instance(
            Services.IMAGE,
            ImageService(config_service=config_service)
        )
    return container.get(Services.IMAGE)


def get_subtitle_service():
    """FastAPI dependency for SubtitleService."""
    return _get_simple_service(
        Services.SUBTITLE,
        "server.domain.artifacts.subtitle_service",
        "SubtitleService"
    )


def get_template_service():
    """FastAPI dependency for TemplateService."""
    return _get_simple_service(
        Services.TEMPLATE,
        "server.domain.system.template_service",
        "TemplateService"
    )


def get_file_service():
    """FastAPI dependency for FileService."""
    return _get_simple_service(
        Services.FILE,
        "server.domain.media.file_service",
        "FileService"
    )


def get_p115_service():
    """FastAPI dependency for P115Service."""
    from server.domain.integration.p115_service import P115Service

    container = get_container()
    if not container.has(Services.P115):
        container.register_instance(
            Services.P115,
            P115Service(config_service=get_config_service())
        )
    return container.get(Services.P115)


def get_history_service():
    """FastAPI dependency for HistoryService."""
    return _get_simple_service(
        Services.HISTORY,
        "server.application.history_service",
        "HistoryService"
    )


def get_scheduler_service():
    """FastAPI dependency for SchedulerService."""
    return _get_simple_service(
        Services.SCHEDULER,
        "server.application.scheduler_service",
        "SchedulerService"
    )


def get_manual_job_service():
    """FastAPI dependency for ManualJobService."""
    return _get_simple_service(
        Services.MANUAL_JOB,
        "server.application.manual_job_service",
        "ManualJobService"
    )


def get_scrape_job_service():
    """FastAPI dependency for ScrapeJobService."""
    return _get_simple_service(
        Services.SCRAPE_JOB,
        "server.application.scrape_job_service",
        "ScrapeJobService"
    )


def get_scraped_file_service():
    """FastAPI dependency for ScrapedFileService."""
    return _get_simple_service(
        Services.SCRAPED_FILE,
        "server.application.scraped_file_service",
        "ScrapedFileService"
    )


def get_scraper_service():
    """FastAPI dependency for ScraperService."""
    from server.application.scraping.service import ScraperService
    container = get_container()
    if not container.has(Services.SCRAPER):
        container.register_instance(
            Services.SCRAPER,
            ScraperService(
                config_service=get_config_service(),
                tmdb_service=get_tmdb_service(),
                parser_service=get_parser_service(),
                nfo_service=get_nfo_service(),
                rename_service=get_rename_service(),
                image_service=get_image_service(),
                subtitle_service=get_subtitle_service(),
                emby_service=get_emby_service(),
            )
        )
    return container.get(Services.SCRAPER)


# =============================================================================
# FastAPI Dependency Functions - Singleton Services
# =============================================================================


def get_websocket_manager():
    """FastAPI dependency for ConnectionManager."""
    return _get_singleton_service(
        Services.WEBSOCKET,
        "server.infrastructure.realtime",
        "ConnectionManager"
    )


def get_watcher_service():
    """FastAPI dependency for WatcherService."""
    return _get_singleton_service(
        Services.WATCHER,
        "server.application.watcher_service",
        "WatcherService"
    )


# =============================================================================
# FastAPI Dependency Functions - Services with Dependencies
# =============================================================================


def get_tmdb_service():
    """FastAPI dependency for TMDBService."""
    from server.domain.metadata.tmdb_service import TMDBService
    container = get_container()
    if not container.has(Services.TMDB):
        config_service = get_config_service()
        container.register_instance(
            Services.TMDB,
            TMDBService(config_service=config_service)
        )
    return container.get(Services.TMDB)


def get_emby_service():
    """FastAPI dependency for EmbyService."""
    from server.domain.integration.emby_service import EmbyService
    container = get_container()
    if not container.has(Services.EMBY):
        config_service = get_config_service()
        container.register_instance(
            Services.EMBY,
            EmbyService(config_service=config_service)
        )
    return container.get(Services.EMBY)


def get_auth_config_service():
    """FastAPI dependency for AuthConfigService."""
    from server.domain.identity.auth_config_service import AuthConfigService
    return AuthConfigService.get_sync()


def get_log_service():
    """FastAPI dependency for LogService."""
    return _get_singleton_service(
        Services.LOG,
        "server.domain.system.log_service",
        "LogService"
    )
