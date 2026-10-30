"""OutputWriter 依赖的门面能力契约。

拆出协作者之后，OutputWriter 仍要经门面取用存储适配器、配置解析与图片下载。
把这些成员收敛成一个 Protocol：

- output_writer 不再 import ScraperService，两者之间的类型循环就此断开；
- 契约只声明 OutputWriter 真正用到的东西，"它需要什么"一目了然；
- 结构化类型不看继承，测试照旧 monkeypatch ScraperService 的实例属性，接缝不变。

注：这些仍是 ScraperService 的私有方法名。改成公开命名要同步改一批测试，
本次只调整依赖方向，不动命名。
"""

from typing import Protocol, runtime_checkable

from server.application.scraping.p115_storage_provider import P115StorageProvider
from server.domain.artifacts.nfo_service import NFOService
from server.domain.artifacts.rename_service import RenameService
from server.domain.system.config_service import ConfigService
from server.models.manual_job import ManualJobAdvancedSettings
from server.models.nfo import SeasonNFO
from server.models.storage import StorageProvider
from server.models.tmdb import TMDBSeason, TMDBSeries


@runtime_checkable
class ScrapingPorts(Protocol):
    """OutputWriter 依赖的门面能力集合。"""

    config_service: ConfigService
    rename_service: RenameService
    nfo_service: NFOService

    def _get_storage_provider(self, provider: StorageProvider) -> P115StorageProvider: ...

    async def _get_effective_nfo_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> dict[str, bool]: ...

    async def _get_effective_download_config(
        self,
        advanced_settings: ManualJobAdvancedSettings | None,
    ) -> dict[str, bool]: ...

    def _get_season_nfo_data(self, series: TMDBSeries, season_num: int) -> SeasonNFO: ...

    async def _download_series_images(
        self,
        series: TMDBSeries,
        series_folder: str,
        download_poster: bool = True,
        download_fanart: bool = True,
    ) -> None: ...

    async def _download_episode_image(
        self,
        season_info: TMDBSeason | None,
        season_num: int,
        episode_num: int,
        season_folder: str,
        video_stem: str,
    ) -> None: ...
