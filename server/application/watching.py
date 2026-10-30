"""监控配置用例 - 保存全局监控配置并同步监控目录表与监控服务。

从 api/config.py 下沉，路由只做协议转换。
"""

from server.application.watcher_service import WatcherService
from server.domain.integration.p115_service import P115Service
from server.domain.system.config_service import ConfigService
from server.models.watcher import WatchedFolderCreate, WatcherConfig


class WatcherConfigUseCase:
    """保存监控配置并同步 watched_folders 表。"""

    def __init__(
        self,
        config_service: ConfigService,
        watcher_service: WatcherService,
        p115_service: P115Service,
    ) -> None:
        self._config_service = config_service
        self._watcher_service = watcher_service
        self._p115_service = p115_service

    async def save_and_sync(self, config: WatcherConfig) -> None:
        """保存配置并按启用状态增删监控目录、启停监控服务。"""
        await self._config_service.save_watcher_config(config)

        watcher_service = self._watcher_service

        if config.enabled and config.watch_dirs:
            # 获取现有的监控目录
            existing_folders, _ = await watcher_service.list_folders()
            existing_paths = {f.path for f in existing_folders}

            # 添加新目录（自动识别 115 路径）
            for dir_path in config.watch_dirs:
                if dir_path not in existing_paths:
                    create_req = WatchedFolderCreate(
                        path=dir_path,
                        enabled=True,
                        mode=config.mode,
                    )
                    # 路径以 /115网盘/ 开头 → 自动设为 115 provider + 解析 file_id
                    if dir_path.startswith("/115网盘"):
                        create_req.provider = "115"
                        try:
                            create_req.file_id = await self._p115_service.resolve_directory_id_by_path(
                                dir_path
                            )
                        except Exception:
                            pass  # 解析失败仍创建（后续轮询时会用路径扫描）
                    await watcher_service.create_folder(create_req)

            # 删除不在列表中的目录
            for folder in existing_folders:
                if folder.path not in config.watch_dirs:
                    await watcher_service.delete_folder(folder.id)

            # 启动监控服务
            await watcher_service.start()
        else:
            # 停止监控服务
            await watcher_service.stop()
