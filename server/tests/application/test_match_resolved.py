"""「匹配已确定即落库」的特性化测试。

背景：重试接口必须带 tmdb_id + 季 + 集，而记录此前只在成功后才写元数据，
一旦超时/失败，用户重试时只能重新搜一遍 TMDB —— 尽管匹配早就定下来了。
本轮给 scrape_file 加了 on_match_resolved 回调（核验季/集通过后触发），
由调用方落库；这两个测试钉住回调的触发时机与「不触发」的分支。
"""

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from server.application.history_service import HistoryService
from server.application.scraping.service import ScraperService
from server.domain.artifacts.nfo_service import NFOService
from server.domain.artifacts.rename_service import RenameService
from server.domain.parsing.parser_service import ParserService
from server.domain.system.config_service import ConfigService
from server.domain.system.template_service import TemplateService
from server.infrastructure.db import create_all_tables
from server.models.emby import ConflictCheckResult, ConflictType
from server.models.history import HistoryRecordCreate, TaskStatus
from server.models.organize import OrganizeMode
from server.models.scraper import ScrapeRequest, ScrapeStatus
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


def _season(episodes: list[TMDBEpisode]) -> TMDBSeason:
    return TMDBSeason(season_number=1, name="Season 1", episodes=episodes)


class FakeTMDB:
    """按需装配的 TMDB 假服务（与 test_scraper_service 同款）。"""

    def __init__(self, season: TMDBSeason | None = None) -> None:
        self.get_series_by_api = AsyncMock(return_value=_series())
        self.get_season_by_api = AsyncMock(
            return_value=season if season is not None else _season([TMDBEpisode(episode_number=1, name="E1")])
        )
        results = [TMDBSearchResult(id=123, name="Test Show", adult=True)]
        self.search_series_by_api = AsyncMock(
            return_value=TMDBSearchResponse(query="test", total_results=len(results), results=results)
        )


def _build_service(temp_db: Path, tmdb: FakeTMDB) -> ScraperService:
    return ScraperService(
        config_service=ConfigService(db_path=temp_db),
        tmdb_service=tmdb,
        parser_service=ParserService(),
        nfo_service=NFOService(),
        rename_service=RenameService(template_service=TemplateService(db_path=temp_db)),
        image_service=None,
        subtitle_service=None,
        emby_service=None,
    )


def _patch_tail(monkeypatch: pytest.MonkeyPatch, service: ScraperService) -> None:
    """控制共享尾部的协作接缝（与本目录 test_scraper_service 一致）。"""
    monkeypatch.setattr(
        service,
        "_check_emby_conflict",
        AsyncMock(return_value=ConflictCheckResult(conflict_type=ConflictType.NO_CONFLICT)),
    )
    monkeypatch.setattr(service, "_get_effective_nfo_config", AsyncMock(return_value={"nfo_enabled": False}))
    monkeypatch.setattr(
        service,
        "_get_effective_download_config",
        AsyncMock(return_value={"download_poster": False, "download_fanart": False, "download_thumb": False}),
    )
    monkeypatch.setattr(service, "_process_subtitles", lambda source, dest: [])


@pytest.mark.asyncio
async def test_scrape_file_notifies_match_once(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """核验通过后回调一次，参数是已定下来的 TMDB ID 与季/集。"""
    src = tmp_path / "Test.Show.S01E01.1080p.mp4"
    src.write_bytes(b"video")

    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service)

    calls: list[tuple[int, int, int]] = []

    async def on_match_resolved(tmdb_id: int, season: int, episode: int) -> None:
        calls.append((tmdb_id, season, episode))

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(tmp_path / "library"),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        ),
        on_match_resolved=on_match_resolved,
    )

    assert result.status == ScrapeStatus.SUCCESS
    # 只报一次：后续步骤（生成 NFO / 搬运）不改匹配，重复落库是多余的写
    assert calls == [(123, 1, 1)]


@pytest.mark.asyncio
async def test_scrape_file_does_not_notify_when_verify_fails(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """季/集核验不通过时匹配并未定下来（要用户重选），不能落库。"""
    src = tmp_path / "Test.Show.S01E01.1080p.mp4"
    src.write_bytes(b"video")

    # 第 1 季里只有第 2 集 → 核验失败
    service = _build_service(temp_db, FakeTMDB(season=_season([TMDBEpisode(episode_number=2, name="E2")])))
    _patch_tail(monkeypatch, service)

    calls: list[tuple[int, int, int]] = []

    async def on_match_resolved(tmdb_id: int, season: int, episode: int) -> None:
        calls.append((tmdb_id, season, episode))

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(tmp_path / "library"),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        ),
        on_match_resolved=on_match_resolved,
    )

    assert result.status == ScrapeStatus.NEED_SEASON_EPISODE
    assert calls == []


@pytest.mark.asyncio
async def test_scrape_file_without_callback_still_succeeds(temp_db: Path, tmp_path: Path, monkeypatch) -> None:
    """回调是可选的：不传也要照常跑完（既有调用点不受影响）。"""
    src = tmp_path / "Test.Show.S01E01.1080p.mp4"
    src.write_bytes(b"video")

    service = _build_service(temp_db, FakeTMDB())
    _patch_tail(monkeypatch, service)

    result = await service.scrape_file(
        ScrapeRequest(
            file_path=str(src),
            output_dir=str(tmp_path / "library"),
            auto_select=True,
            link_mode=OrganizeMode.COPY,
        )
    )

    assert result.status == ScrapeStatus.SUCCESS


@pytest.mark.asyncio
async def test_update_record_persists_tmdb_id(temp_db: Path, tmp_path: Path) -> None:
    """落库与读回：超时/失败记录也能给出「匹配的是哪部剧、第几季第几集」。"""
    async with aiosqlite.connect(temp_db) as db:
        await create_all_tables(db)
        await db.commit()

    service = HistoryService(db_path=temp_db)
    record = await service.create_record(
        HistoryRecordCreate(
            task_name="测试记录",
            folder_path=str(tmp_path / "作品名 第1話.mp4"),
            status=TaskStatus.RUNNING,
            total_files=1,
            success_count=0,
            failed_count=0,
            duration_seconds=0,
        )
    )
    # 新记录默认没有匹配
    assert record.tmdb_id is None

    await service.update_record(record.id, tmdb_id=85174, season_number=1, episode_number=7)

    listed, _total = await service.list_records(limit=10, offset=0)
    assert listed[0].tmdb_id == 85174
    assert listed[0].season_number == 1
    assert listed[0].episode_number == 7

    detail = await service.get_record(record.id)
    assert detail is not None
    assert detail.tmdb_id == 85174
    assert detail.episode_number == 7
