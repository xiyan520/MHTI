"""刮削任务超时的定位与回写。

超时本身在 ``scrape_job_service._execute_scrape_job`` 里由 ``asyncio.wait_for`` 触发，
测试不真跑一遍 10 秒以上的任务，而是覆盖四部分：
- ``resolve_timeout_step``：超时前最后执行到哪一步（日志末项）
- ``build_timeout_message`` / ``build_timeout_step``：给列表与时间轴用的文案
- ``record_scrape_timeout``：追加超时兜底步骤（不动前面步骤的完成状态）
- ``_execute_scrape_job`` 的超时分支：真实跑一遍处理器，断言落库字段与日志现场
"""

import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
import pytest_asyncio

from server.application.history_service import HistoryService
from server.application.scrape_job_service import (
    TIMEOUT_STEP_NAME,
    build_timeout_message,
    build_timeout_step,
    record_scrape_timeout,
    resolve_timeout_step,
)
from server.infrastructure.db import create_all_tables
from server.models.history import (
    HistoryRecordCreate,
    ScrapeLogEntry,
    ScrapeLogLevel,
    ScrapeLogStep,
    TaskStatus,
)


class _RecordingHistoryService:
    """只记调用参数的假历史服务，用于观察回写内容。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []

    async def update_scrape_logs(self, record_id: str, logs: list) -> None:
        self.calls.append((record_id, logs))


def _step(name: str, completed: bool = True, message: str | None = None) -> ScrapeLogStep:
    logs = [ScrapeLogEntry(message=message)] if message else []
    return ScrapeLogStep(name=name, completed=completed, logs=logs)


# ============================================================================
# resolve_timeout_step
# ============================================================================


class TestResolveTimeoutStep:
    def test_empty_logs_has_no_step(self):
        assert resolve_timeout_step([]) is None

    def test_last_step_is_the_running_node(self):
        logs = [_step("解析文件名"), _step("搜索 TMDB"), _step("获取详情")]
        assert resolve_timeout_step(logs) == "获取详情"

    def test_ignores_its_own_fallback_step(self):
        """重跑时旧日志里可能已有一层超时兜底步骤，不能把它当节点。"""
        logs = [_step("搜索 TMDB"), _step(TIMEOUT_STEP_NAME, completed=False)]
        assert resolve_timeout_step(logs) == "搜索 TMDB"

    def test_only_fallback_step_has_no_real_node(self):
        assert resolve_timeout_step([_step(TIMEOUT_STEP_NAME, completed=False)]) is None


# ============================================================================
# 文案
# ============================================================================


class TestTimeoutCopy:
    def test_message_names_the_node(self):
        assert (
            build_timeout_message("搜索 TMDB", 30)
            == "任务超时（超过 30 秒，最后停在「搜索 TMDB」节点）"
        )

    def test_message_without_node_says_not_yet_started(self):
        assert build_timeout_message(None, 30) == "任务超时（超过 30 秒，尚未进入刮削步骤）"

    def test_fallback_step_records_node_threshold_and_elapsed(self):
        step = build_timeout_step("搜索 TMDB", 30, 30.4)

        assert step.name == TIMEOUT_STEP_NAME
        assert step.completed is False
        assert len(step.logs) == 3
        assert step.logs[0].level == ScrapeLogLevel.ERROR
        assert "搜索 TMDB" in step.logs[0].message
        # 阈值与「不会自动重试」都要写进时间轴，用户不用去别处找机制说明
        assert "30 秒" in step.logs[1].message
        assert "30.4 秒" in step.logs[2].message
        assert "重试刮削" in step.logs[2].message
        assert all(log.level == ScrapeLogLevel.WARNING for log in step.logs[1:])

    def test_fallback_step_without_node_says_preparation_stage(self):
        step = build_timeout_step(None, 30, 30.0)
        assert "准备阶段" in step.logs[0].message


# ============================================================================
# record_scrape_timeout
# ============================================================================


class TestRecordScrapeTimeout:
    @pytest.mark.asyncio
    async def test_appends_fallback_after_the_touched_step(self):
        service = _RecordingHistoryService()
        logs = [_step("解析文件名"), _step("搜索 TMDB", message="搜索关键词: 作品名")]

        await record_scrape_timeout(
            history_service=service,
            record_id="rec-1",
            logs=logs,
            step_name="搜索 TMDB",
            timeout_seconds=30,
            elapsed=30.2,
        )

        assert service.calls == [("rec-1", logs)]
        assert logs[-1].name == TIMEOUT_STEP_NAME
        # 不改前面步骤的完成状态：末步可能是真跑完了（超时卡在步骤之间的尾巴）
        assert logs[1].completed is True
        # 原步骤的日志不能被冲掉
        assert logs[1].logs[0].message == "搜索关键词: 作品名"

    @pytest.mark.asyncio
    async def test_without_any_logs_only_appends_fallback(self):
        service = _RecordingHistoryService()

        await record_scrape_timeout(
            history_service=service,
            record_id="rec-2",
            logs=[],
            step_name=None,
            timeout_seconds=30,
            elapsed=30.0,
        )

        assert len(service.calls[0][1]) == 1
        assert service.calls[0][1][0].name == TIMEOUT_STEP_NAME


# ============================================================================
# 落库与读回
# ============================================================================


async def _initialize_test_db(db_path: Path) -> None:
    async with aiosqlite.connect(db_path) as db:
        await create_all_tables(db)
        await db.commit()


@pytest_asyncio.fixture
async def history_service(temp_db: Path) -> HistoryService:
    await _initialize_test_db(temp_db)
    return HistoryService(db_path=temp_db)


@pytest.mark.asyncio
async def test_update_record_persists_timeout_fields(history_service: HistoryService):
    record = await history_service.create_record(
        HistoryRecordCreate(
            task_name="文件刮削任务 #1",
            folder_path="D:/media/EP01.mkv",
            status=TaskStatus.RUNNING,
            total_files=1,
            success_count=0,
            failed_count=0,
            duration_seconds=0,
        )
    )

    await history_service.update_record(
        record.id,
        status=TaskStatus.TIMEOUT,
        error_message=build_timeout_message("搜索 TMDB", 30),
        duration_seconds=30.3,
        timeout_step="搜索 TMDB",
        timeout_seconds=30,
    )

    detail = await history_service.get_record(record.id)
    assert detail is not None
    assert detail.status == TaskStatus.TIMEOUT
    assert detail.timeout_step == "搜索 TMDB"
    assert detail.timeout_seconds == 30
    assert detail.error_message == "任务超时（超过 30 秒，最后停在「搜索 TMDB」节点）"

    listed, _total = await history_service.list_records(limit=1, offset=0)
    assert listed[0].timeout_step == "搜索 TMDB"
    assert listed[0].timeout_seconds == 30


@pytest.mark.asyncio
async def test_records_without_timeout_have_null_fields(history_service: HistoryService):
    record = await history_service.create_record(
        HistoryRecordCreate(
            task_name="文件刮削任务 #2",
            folder_path="D:/media/EP02.mkv",
            status=TaskStatus.SUCCESS,
            total_files=1,
            success_count=1,
            failed_count=0,
            duration_seconds=3.0,
        )
    )

    detail = await history_service.get_record(record.id)
    assert detail is not None
    assert detail.timeout_step is None
    assert detail.timeout_seconds is None

# ============================================================================
# 真跑一次 _execute_scrape_job 的超时分支
# ============================================================================


class _HangingScraper:
    """模拟卡在 TMDB 搜索：先报两步日志，再以 asyncio.TimeoutError 结束。

    真实链路上超时由 ``asyncio.wait_for`` 抛出（它 cancel 掉正在跑的协程），
    这里直接抛同类型异常，走的仍是同一段 except 分支，且不必真等 10 秒。
    """

    def __init__(self) -> None:
        self.requests: list = []

    async def scrape_file(self, request, on_log_update=None, on_match_resolved=None):
        self.requests.append(request)
        if on_log_update:
            await on_log_update([
                _step("解析文件名", message="解析结果: 作品名 S1E1"),
                _step("搜索 TMDB", message="搜索关键词: 作品名"),
            ])
        # 匹配在超时前已确定：生产链路上这一刻就会落库，超时后仍能看出匹配的是哪部剧
        if on_match_resolved:
            await on_match_resolved(85174, 1, 7)
        raise asyncio.TimeoutError


class _StubConfigService:
    """只提供超时阈值，避免测试写真实系统设置。"""

    def __init__(self) -> None:
        self.timeout_seconds = 10

    async def get_system_config(self):
        return SimpleNamespace(task_timeout=self.timeout_seconds)


class _StubNotifier:
    """占位推送器：测试不需要真实 WebSocket 广播。"""

    async def notify_progress(self, *args, **kwargs) -> None: ...
    async def notify_failed(self, *args, **kwargs) -> None: ...
    async def notify_history_created(self, *args, **kwargs) -> None: ...
    async def notify_history_updated(self, *args, **kwargs) -> None: ...
    async def notify_history_detail_update(self, *args, **kwargs) -> None: ...
    async def notify_history_deleted(self, *args, **kwargs) -> None: ...
    async def notify_history_cleared(self, *args, **kwargs) -> None: ...
    async def notify_history_restored(self, *args, **kwargs) -> None: ...


@pytest.mark.asyncio
async def test_execute_scrape_job_writes_timeout_node(temp_db: Path, monkeypatch, tmp_path: Path):
    """超时分支要写清：停在「搜索 TMDB」、阈值 10 秒，并把该步标为未完成。"""
    from server.application import scrape_job_service as sjs
    from server.application.history_service import HistoryService
    from server.application.scraped_file_service import ScrapedFileService
    from server.models.scrape_job import ScrapeJobCreate, ScrapeJobSource, ScrapeJobStatus

    await _initialize_test_db(temp_db)
    history = HistoryService(db_path=temp_db)
    job_service = sjs.ScrapeJobService(db_path=temp_db)
    scraper = _HangingScraper()

    monkeypatch.setattr("server.application.history_service.HistoryService", lambda *a, **k: history)
    monkeypatch.setattr(
        "server.application.scraped_file_service.ScrapedFileService",
        lambda *a, **k: ScrapedFileService(db_path=temp_db),
    )
    monkeypatch.setattr(
        "server.domain.system.config_service.ConfigService", lambda *a, **k: _StubConfigService()
    )
    monkeypatch.setattr("server.bootstrap.get_scraper_service", lambda: scraper)
    monkeypatch.setattr("server.application.scrape_job_service.get_notifier", lambda: _StubNotifier())
    monkeypatch.setattr("server.application.history_service.get_notifier", lambda: _StubNotifier())

    # 直接落库建任务：走 create_job 会把任务塞进真实 worker 队列，测试不需要
    source_file = tmp_path / "作品名 第1話.mp4"
    source_file.write_bytes(b"video")
    create = ScrapeJobCreate(
        file_path=str(source_file), output_dir=str(tmp_path / "out"), source=ScrapeJobSource.MANUAL
    )
    job_id = "timeoutjob"
    await job_service._repo.insert_job(
        job_id,
        create,
        created_at=datetime.now().isoformat(),
        advanced_settings_json=None,
        file_locator_json=None,
        output_locator_json=None,
        metadata_locator_json=None,
    )

    await sjs._execute_scrape_job(job_service, job_id)

    records, _total = await history.list_records(limit=10, offset=0)
    assert len(records) == 1
    record = records[0]
    assert record.status == TaskStatus.TIMEOUT
    assert record.timeout_step == "搜索 TMDB"
    assert record.timeout_seconds == 10
    # 超时前确定的匹配要留在记录里，重试直接沿用（不必重新搜索）
    assert record.tmdb_id == 85174
    assert record.season_number == 1
    assert record.episode_number == 7
    assert record.error_message == "任务超时（超过 10 秒，最后停在「搜索 TMDB」节点）"

    detail = await history.get_record(record.id)
    assert detail is not None
    assert [step.name for step in detail.scrape_logs] == ["解析文件名", "搜索 TMDB", TIMEOUT_STEP_NAME]
    assert detail.scrape_logs[1].logs[0].message == "搜索关键词: 作品名"
    assert detail.scrape_logs[-1].completed is False

    job = await job_service.get_job(job_id)
    assert job is not None
    assert job.status == ScrapeJobStatus.TIMEOUT
    assert job.error_message == record.error_message
    assert job.history_record_id == record.id
