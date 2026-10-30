"""ScraperService 编排共享尾部的特性化测试。

覆盖 scrape_file / scrape_by_id 的公共尾部路径：
核验季/集 → Emby 冲突 → NFO 生成 → 文件移动/整理 → 图片/字幕钩子 → 状态回写。

模式与 test_rename_service 一致：实例级 monkeypatch 控制外部协作
（_check_emby_conflict / _get_effective_* / _process_subtitles / _get_storage_provider），
真实的 Parser/NFO/Rename 参与，锁定当前行为供后续结构化拆分对照。
"""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from server.application.scraping.ports import ScrapingPorts
from server.application.scraping.service import ScraperService
from server.domain.artifacts.nfo_service import NFOService
from server.domain.artifacts.rename_service import RenameService
from server.domain.parsing.parser_service import ParserService
from server.domain.system.config_service import ConfigService
from server.domain.system.template_service import TemplateService
from server.models.emby import ConflictCheckResult, ConflictType
from server.models.organize import OrganizeMode
from server.models.rename import RenameRequest
from server.models.scraper import ScrapeByIdRequest, ScrapeRequest, ScrapeStatus
from server.models.storage import StorageLocator, StorageProvider
from server.models.tmdb import (
    TMDBEpisode,
    TMDBSearchResponse,
    TMDBSearchResult,
    TMDBSeason,
    TMDBSeries,
)


def _series() -> TMDBSeries:
    return TMDBSeries(
        id=123,
        name="Test Show",
        original_name="Test Show",
        first_air_date=date(2024, 1, 1),
        number_of_seasons=1,
        number_of_episodes=1,
        seasons=[TMDBSeason(season_number=1, name="Season 1")],
    )


def _season(episodes: list[TMDBEpisode] | None = None) -> TMDBSeason:
    return TMDBSeason(
        season_number=1,
        name="Season 1",
        episodes=episodes if episodes is not None else [TMDBEpisode(episode_number=1, name="Episode 1")],
    )


class FakeTMDB:
    """按需装配的 TMDB 假服务。"""

    def __init__(
        self,
        series: TMDBSeries | None = None,
        season: TMDBSeason | None = None,
        search_results: list[TMDBSearchResult] | None = None,
    ) -> None:
        self.get_series_by_api = AsyncMock(return_value=series if series is not None else _series())
        self.get_season_by_api = AsyncMock(return_value=season if season is not None else _season())
        results = search_results if search_results is not None else []
        self.search_series_by_api = AsyncMock(
            return_value=TMDBSearchResponse(query="test", total_results=len(results), results=results)
        )


def _build_service(temp_db: Path, tmdb: FakeTMDB, parser=None) -> ScraperService:
    return ScraperService(
        config_service=ConfigService(db_path=temp_db),
        tmdb_service=tmdb,
        parser_service=parser,
        nfo_service=NFOService(),
        rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
        image_service=None,
        subtitle_service=None,
        emby_service=None,
    )


def _patch_tail(
    monkeypatch: pytest.MonkeyPatch,
    service: ScraperService,
    *,
    emby_conflict: ConflictType = ConflictType.NO_CONFLICT,
    nfo_enabled: bool = True,
    subtitle_calls: list[tuple[str, str]] | None = None,
) -> None:
    """控制共享尾部的协作接缝（与线上调用点保持 self.<原名> 一致）。"""
    monkeypatch.setattr(
        service,
        "_check_emby_conflict",
        AsyncMock(
            return_value=ConflictCheckResult(
                conflict_type=emby_conflict,
                message="Emby 中已存在该集" if emby_conflict != ConflictType.NO_CONFLICT else None,
            )
        ),
    )
    monkeypatch.setattr(service, "_get_effective_nfo_config", AsyncMock(return_value={"nfo_enabled": nfo_enabled}))
    monkeypatch.setattr(
        service,
        "_get_effective_download_config",
        AsyncMock(return_value={"download_poster": False, "download_fanart": False, "download_thumb": False}),
    )

    def _fake_process_subtitles(source: str, dest: str) -> list[str]:
        if subtitle_calls is not None:
            subtitle_calls.append((source, dest))
        return []

    monkeypatch.setattr(service, "_process_subtitles", _fake_process_subtitles)


def _step_names(result) -> list[str]:
    return [step.name for step in (result.scrape_logs or [])]


# =============================================================================
# scrape_by_id：本地路径全链路
# =============================================================================


@pytest.mark.asyncio
async def test_scrape_by_id_local_success_full_tail(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """本地文件成功路径：核验→Emby→NFO→复制→图片/字幕钩子→SUCCESS。"""
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"
    subtitles: list[tuple[str, str]] = []

    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service, subtitle_calls=subtitles)

    result = await service.scrape_by_id(
        ScrapeByIdRequest(
            file_path=str(src),
            tmdb_id=123,
            season=1,
            episode=1,
            output_dir=str(out_dir),
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.SUCCESS
    assert result.parsed_season == 1
    assert result.parsed_episode == 1
    assert result.series_info is not None

    dest = Path(result.dest_path)
    assert dest.exists()
    assert dest.parent.name == "Season 1"
    assert dest.parent.parent.name == "Test Show (2024)"
    assert dest.read_bytes() == b"video"

    # NFO 写入目标季度目录（metadata_dir 未配置 → 与视频同目录）
    assert Path(result.nfo_path).exists()
    assert Path(result.nfo_path) == dest.parent / f"{dest.stem}.nfo"

    # 步骤日志覆盖共享尾部
    names = _step_names(result)
    assert "获取详情" in names
    assert "确定季/集" not in names  # scrape_by_id 不产生该步骤
    assert "生成 NFO" in names
    assert any("文件" in n for n in names)

    # 核验信息记录在"获取详情"步骤的日志中（scrape_by_id 不单独建核验步骤）
    all_messages = [log.message for step in result.scrape_logs for log in step.logs]
    assert any("核验通过" in m for m in all_messages)

    # 字幕钩子在整理后以（源, 目标）调用一次
    assert len(subtitles) == 1
    assert subtitles[0][1] == str(dest)


@pytest.mark.asyncio
async def test_scrape_by_id_verify_fails_need_season_episode(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """季存在但集不存在：返回 NEED_SEASON_EPISODE，文件未被移动。"""
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    tmdb = FakeTMDB(season=_season(episodes=[TMDBEpisode(episode_number=2, name="Episode 2")]))
    service = _build_service(temp_db, tmdb)
    _patch_tail(monkeypatch, service)

    result = await service.scrape_by_id(
        ScrapeByIdRequest(
            file_path=str(src),
            tmdb_id=123,
            season=1,
            episode=1,
            output_dir=str(out_dir),
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.NEED_SEASON_EPISODE
    assert "不存在第 1 集" in (result.message or "")
    assert src.exists()
    assert not out_dir.exists()


@pytest.mark.asyncio
async def test_scrape_by_id_has_no_emby_check(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """钉住现状：scrape_by_id 路径不执行 Emby 冲突检查。

    与 scrape_file 的差异（后者有 Emby 检查步骤）；拆共享尾部时必须保留此差异。
    """
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service, emby_conflict=ConflictType.EPISODE_EXISTS)

    result = await service.scrape_by_id(
        ScrapeByIdRequest(
            file_path=str(src),
            tmdb_id=123,
            season=1,
            episode=1,
            output_dir=str(out_dir),
            link_mode=OrganizeMode.COPY,
        )
    )

    # 即使冲突检查返回"已存在"，本路径也不调用它、不因之暂停
    assert service._check_emby_conflict.await_count == 0  # type: ignore[attr-defined]
    assert result.status == ScrapeStatus.SUCCESS


@pytest.mark.asyncio
async def test_scrape_by_id_file_conflict_reports_dest(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """目标文件已存在：返回 FILE_CONFLICT 并给出目标路径。"""
    src = tmp_path / "input.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    rename_service = RenameService(template_service=TemplateService(db_path=temp_db))
    preview = rename_service.preview_rename(
        RenameRequest(
            source_path=str(src),
            title="Test Show",
            season=1,
            episode=1,
            year=2024,
            output_dir=str(out_dir),
            link_mode=OrganizeMode.COPY,
        )
    )
    occupied = Path(preview.dest_path)
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"existing")

    service = ScraperService(
        config_service=ConfigService(db_path=temp_db),
        tmdb_service=FakeTMDB(),
        parser_service=None,
        nfo_service=NFOService(),
        rename_service=rename_service,
        image_service=None,
        subtitle_service=None,
        emby_service=None,
    )
    _patch_tail(monkeypatch, service)

    result = await service.scrape_by_id(
        ScrapeByIdRequest(
            file_path=str(src),
            tmdb_id=123,
            season=1,
            episode=1,
            output_dir=str(out_dir),
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.FILE_CONFLICT
    assert result.dest_path == str(occupied)
    assert "目标文件已存在" in (result.message or "")
    assert occupied.read_bytes() == b"existing"
    # 源文件未被清理（复制模式下冲突不动源）
    assert src.exists()


# =============================================================================
# scrape_file：搜索/选择分支 + 共享尾部
# =============================================================================


@pytest.mark.asyncio
async def test_scrape_file_success_auto_select(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """单结果自动选择：解析→搜索→选择→共享尾部→SUCCESS。"""
    src = tmp_path / "Test.Show.S01E01.1080p.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    tmdb = FakeTMDB(search_results=[TMDBSearchResult(id=123, name="Test Show", adult=True)])
    service = _build_service(temp_db, tmdb, parser=ParserService())
    _patch_tail(monkeypatch, service)

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(out_dir),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.SUCCESS
    assert result.selected_id == 123
    assert Path(result.dest_path).exists()

    names = _step_names(result)
    assert "解析文件名" in names
    assert "搜索 TMDB" in names
    assert "确定季/集" in names
    assert any("核验" in n for n in names)
    assert "生成 NFO" in names


@pytest.mark.asyncio
async def test_scrape_file_emby_conflict_pauses(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """scrape_file：Emby 冲突返回 EMBY_CONFLICT 并携带详情，文件未被移动。"""
    src = tmp_path / "Test.Show.S01E01.1080p.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    tmdb = FakeTMDB(search_results=[TMDBSearchResult(id=123, name="Test Show", adult=True)])
    service = _build_service(temp_db, tmdb, parser=ParserService())
    _patch_tail(monkeypatch, service, emby_conflict=ConflictType.EPISODE_EXISTS)

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(out_dir),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.EMBY_CONFLICT
    assert result.emby_conflict is not None
    assert result.emby_conflict.conflict_type == ConflictType.EPISODE_EXISTS
    assert "Emby 冲突检查" in _step_names(result)
    assert not out_dir.exists()


@pytest.mark.asyncio
async def test_scrape_file_multi_results_need_selection(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """多结果需要用户选择：返回 NEED_SELECTION 并携带富化后的搜索结果。"""
    src = tmp_path / "Test.Show.S01E01.1080p.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    tmdb = FakeTMDB(
        search_results=[
            TMDBSearchResult(id=123, name="Test Show", adult=True),
            TMDBSearchResult(id=456, name="Test Show 2", adult=True),
        ]
    )
    service = _build_service(temp_db, tmdb, parser=ParserService())
    _patch_tail(monkeypatch, service)

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(out_dir),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.NEED_SELECTION
    assert result.search_results is not None
    assert len(result.search_results) == 2
    # 富化调用经真实 _enrich_search_results（FakeTMDB 对每个结果返回同一详情）
    assert tmdb.get_series_by_api.await_count == 2
    assert not out_dir.exists()


# =============================================================================
# 115→115：provider 输出 + 本地元数据落盘（_write_local_metadata_only 覆盖）
# =============================================================================


@pytest.mark.asyncio
async def test_scrape_by_id_115_to_115_writes_local_metadata(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """115→115：走 provider 移动，NFO/剧集/季度文件落本地元数据目录。"""
    meta_dir = tmp_path / "meta"

    class FakeProvider:
        def __init__(self):
            self.ensure_calls: list = []
            self.rename_calls: list = []
            self.copy_calls: list = []

        async def ensure_directory(self, locator, relative_path):
            self.ensure_calls.append((locator, relative_path))
            return {"id": "season-dir", "parent_id": locator.file_id}

        async def rename(self, locator, target_name, target_parent_id):
            self.rename_calls.append((locator, target_name, target_parent_id))
            return {"state": True}

        async def copy(self, locator, target_name, target_parent_id):
            self.copy_calls.append((locator, target_name, target_parent_id))
            return {"state": True}

    provider = FakeProvider()
    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service)
    monkeypatch.setattr(service, "_get_storage_provider", lambda provider_name: provider)

    file_locator = StorageLocator(
        provider=StorageProvider.P115,
        path="/115网盘/待整理/episode.mp4",
        file_id="file-001",
        parent_id="scan-root",
        is_dir=False,
    )
    output_locator = StorageLocator(
        provider=StorageProvider.P115,
        path="/115网盘/已整理",
        file_id="target-root",
        parent_id="0",
        is_dir=True,
    )

    result = await service.scrape_by_id(
        ScrapeByIdRequest(
            file_path=file_locator.path,
            tmdb_id=123,
            season=1,
            episode=1,
            file_locator=file_locator,
            output_locator=output_locator,
            metadata_dir=str(meta_dir),
            link_mode=OrganizeMode.MOVE,
        )
    )

    assert result.status == ScrapeStatus.SUCCESS
    assert provider.rename_calls, "MOVE 模式应调用 provider.rename"
    assert not provider.copy_calls

    series_folder = meta_dir / "Test Show (2024)"
    season_folder = series_folder / "Season 1"
    assert season_folder.exists()
    nfo_files = sorted(p.name for p in season_folder.glob("*.nfo"))
    assert nfo_files == ["Test Show - S01E01 -.nfo", "season.nfo"]
    assert (series_folder / "tvshow.nfo").exists()


@pytest.mark.asyncio
async def test_scrape_file_stops_when_adult_access_disabled(temp_db: Path):
    """Token 未开 R18 时搜索结果必然为空。

    过去用户只会看到「未找到匹配的成人剧集」，无从判断是权限问题；现在应在
    搜索前就停下来，并明确指向设置页。
    """
    tmp_path = Path(temp_db).parent
    video = tmp_path / "剧名 第1話.mp4"
    video.write_bytes(b"video")

    tmdb = FakeTMDB(search_results=[TMDBSearchResult(id=123, name="Test Show", adult=True)])
    service = _build_service(temp_db, tmdb, parser=ParserService())
    service.config_service.get_adult_status = AsyncMock(return_value=(False, "未开启 R18", None))

    result = await service.scrape_file(
        ScrapeRequest(file_path=str(video), output_dir=str(tmp_path / "out"))
    )

    assert result.status == ScrapeStatus.API_FAILED
    assert "R18" in (result.message or "")
    tmdb.search_series_by_api.assert_not_awaited()
    assert [step.name for step in result.scrape_logs][:2] == ["解析文件名", "检查 TMDB 权限"]


def test_scraper_service_satisfies_scraping_ports(temp_db: Path):
    """OutputWriter 只依赖 ScrapingPorts 契约：门面必须把成员都提供出来。

    这条断言代替「output_writer 直接 import ScraperService」：以后谁把门面上的
    _get_storage_provider / _download_* 改名或搬走，这里会先红，而不是等到运行时
    AttributeError。
    """
    service = _build_service(temp_db, FakeTMDB())

    assert isinstance(service, ScrapingPorts)


@pytest.mark.asyncio
async def test_search_falls_back_when_title_starts_with_studio(
    temp_db: Path, tmp_path: Path, monkeypatch
) -> None:
    """剧名粘着制作组时，第一次关键词 0 条，应自动降级重搜并选中结果。"""
    src = tmp_path / "260925魔人○○交配_第一話_chs.mp4"
    src.write_bytes(b"video")
    out_dir = tmp_path / "library"

    empty = TMDBSearchResponse(query="魔人○○交配", total_results=0, results=[])
    hit = TMDBSearchResponse(
        query="○○交配",
        total_results=1,
        results=[TMDBSearchResult(id=85174, name="○○交配", adult=True)],
    )
    tmdb = FakeTMDB()
    tmdb.search_series_by_api = AsyncMock(side_effect=[empty, hit])
    service = _build_service(temp_db, tmdb, parser=ParserService())
    _patch_tail(monkeypatch, service)

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(out_dir),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.SUCCESS
    assert result.selected_id == 85174

    keywords = [call.args[0] for call in tmdb.search_series_by_api.await_args_list]
    assert keywords == ["魔人○○交配", "○○交配"]

    messages = [
        entry.message
        for step in result.scrape_logs
        if step.name == "搜索 TMDB"
        for entry in step.logs
    ]
    # 日志要能区分「TMDB 没搜到」和「搜到但都不是成人内容」
    assert any("TMDB 返回 0 条" in message for message in messages)
    assert any("搜索关键词: ○○交配" in message for message in messages)
