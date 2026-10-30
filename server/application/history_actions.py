"""历史记录刮削用例 - 重试/冲突处理/重新整理后的刮削执行编排。

从 api/history.py 下沉，路由只做协议转换与校验。
"""

import logging
from datetime import date, datetime

from fastapi import HTTPException

from server.application.history_service import HistoryService, build_record_folder_path
from server.application.rescrape_source import RescrapeInput, RescrapeSource
from server.application.scrape_job_service import ScrapeJobService
from server.application.scraped_file_service import ScrapedFileService
from server.models.history import (
    HistoryRecord,
    HistoryRecordDetail,
    ScrapeLogEntry,
    ScrapeLogLevel,
    ScrapeLogStep,
    TaskStatus,
)
from server.models.scraper import ScrapeByIdRequest
from server.models.tmdb import TMDBEpisode, TMDBSeason, TMDBSeries

logger = logging.getLogger(__name__)

# 存档里保存的是完整图片地址（下载流程用的是 TMDB path），重新整理时需还原
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p/"


def _to_tmdb_path(url: str | None) -> str | None:
    """把存档的完整图片地址还原成 TMDB path（形如 /abc.jpg）。"""
    if not url or not url.startswith(_TMDB_IMAGE_BASE):
        return None
    rest = url[len(_TMDB_IMAGE_BASE):]
    # 去掉尺寸段（w500 / original / w300 …）
    return rest.split("/", 1)[1] if "/" in rest else None


def _to_date(value: str | None) -> date | None:
    """存档里的日期是字符串，解析失败按无日期处理。"""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def rebuild_metadata(
    record: "HistoryRecordDetail | HistoryRecord",
    *,
    tmdb_id: int,
    season_num: int,
    episode_num: int,
) -> tuple[TMDBSeries, TMDBSeason]:
    """用历史记录里已存的元数据重建 TMDB 模型（重新整理不请求 TMDB）。

    存档里没有整部剧的季列表，所以 seasons 只放当前这一季——季 NFO 因此只能拿到季号，
    这是「不请求 TMDB」这个前提的必然代价。
    """
    episode = TMDBEpisode(
        episode_number=episode_num,
        name=record.episode_title or f"第 {episode_num} 集",
        overview=record.episode_overview,
        air_date=_to_date(record.episode_air_date),
        still_path=_to_tmdb_path(record.episode_still_url),
    )
    season = TMDBSeason(
        season_number=season_num,
        name=f"第 {season_num} 季",
        episodes=[episode],
        episode_count=1,
    )
    series = TMDBSeries(
        id=tmdb_id,
        name=record.title or f"TMDB {tmdb_id}",
        original_name=record.original_title,
        overview=record.plot,
        first_air_date=_to_date(record.release_date),
        vote_average=record.rating,
        poster_path=_to_tmdb_path(record.poster_url),
        genres=list(record.tags or []),
        seasons=[season],
    )
    return series, season


class HistoryScrapeActions:
    """对历史记录执行刮削并回写状态的用例。"""

    def __init__(
        self,
        history_service: HistoryService,
        scrape_job_service: ScrapeJobService,
        scraped_file_service: ScrapedFileService,
        file_service=None,
    ) -> None:
        self._history_service = history_service
        self._scrape_job_service = scrape_job_service
        self._scraped_file_service = scraped_file_service
        # 记录文件清单/清理服务；缺省时重刮只解析输入，不动旧产物
        self._file_service = file_service

    def _resolver(self) -> RescrapeSource:
        """重跑输入解析与旧产物清理（重刮与重新整理共用）。"""
        return RescrapeSource(self._scraped_file_service, self._file_service)

    async def fill_organize_params(self, record, scrape_request) -> None:
        """补齐重跑的输出参数：整理/元数据目录与整理模式。

        conflict_data 只在走过「处理」的记录上有；扫描流程创建的任务（重刮的主要对象）
        把这三个参数存在任务行上。缺了会退化成「原地重命名」——产物落到源文件旁边，
        而不是整理目录，所以重刮必须先补齐。
        """
        if not record.scrape_job_id:
            return
        if (
            scrape_request.output_dir
            and scrape_request.metadata_dir
            and scrape_request.link_mode
        ):
            return

        try:
            job = await self._scrape_job_service.get_job(record.scrape_job_id)
        except Exception as exc:  # noqa: BLE001 - 参数补齐失败就走原有默认值，不阻断重刮
            logger.warning("读取任务参数失败 %s: %s", record.scrape_job_id, exc)
            return
        if job is None:
            return

        scrape_request.output_dir = scrape_request.output_dir or job.output_dir
        scrape_request.metadata_dir = scrape_request.metadata_dir or job.metadata_dir
        scrape_request.link_mode = scrape_request.link_mode or job.link_mode

    async def prepare_input(
        self, record, *, remote: bool = False
    ) -> tuple[RescrapeInput, list[ScrapeLogStep], str]:
        """解析重跑输入 + 输入让位 + 清理旧产物。

        返回（输入、要追加的日志步骤、登记行该写的源路径）。源文件不可用而改用产物时，
        登记行的 source_path 保持原值（记录的身份是那个源文件），只刷新产物路径。
        """
        resolver = self._resolver()
        input_ = await resolver.resolve(record, remote=remote)
        input_ = await resolver.stage(input_)

        steps: list[ScrapeLogStep] = []
        logs = [
            ScrapeLogEntry(message=f"源文件: {input_.staged_from or input_.path}"),
        ]
        if input_.from_product:
            logs.append(
                ScrapeLogEntry(
                    message="源文件已不存在，改用整理后的产物作为输入",
                    level=ScrapeLogLevel.WARNING,
                )
            )
        if input_.staged_from:
            logs.append(
                ScrapeLogEntry(
                    message=f"产物已临时移出原路径以保证不与自己冲突: {input_.path}",
                )
            )
        steps.append(ScrapeLogStep(name="准备输入", completed=True, logs=logs))

        keep = {input_.path}
        if input_.staged_from:
            keep.add(input_.staged_from)
        deleted = await resolver.clear_products(record.id, keep_paths=keep)
        cleanup_logs = [
            ScrapeLogEntry(message=f"已删除旧产物: {path}") for path in deleted
        ]
        if not cleanup_logs:
            cleanup_logs.append(ScrapeLogEntry(message="没有需要清理的旧产物"))
        steps.append(ScrapeLogStep(name="清理旧产物", completed=True, logs=cleanup_logs))

        registration_source = "" if input_.from_product else input_.path
        return input_, steps, registration_source

    async def _finalize_prepared(
        self,
        *,
        record_id: str,
        result,
        prepared: RescrapeInput | None,
        manual_job_id: int | None,
        duration_seconds: float,
        registration_source: str | None,
    ) -> dict:
        """收尾：写回状态/登记/路径，并处理让位文件的去留。

        让位文件（上一轮产物的副本）成功后才删，失败则改名回原位——
        磁盘上不能留 .mhti-rescrape 临时文件，也不能把产物弄丢。
        """
        folder_path = None
        if prepared is not None:
            folder_path = build_record_folder_path(
                prepared.staged_from or prepared.path, result.dest_path
            )

        try:
            response = await self._finalize(
                record_id,
                result,
                manual_job_id=manual_job_id,
                duration_seconds=duration_seconds,
                folder_path=folder_path,
                replace_registration=prepared is not None,
                registration_source=registration_source,
            )
        except Exception:
            await self._release_input(prepared, dest_path=None, success=False)
            raise

        await self._release_input(prepared, dest_path=result.dest_path, success=True)
        return response

    async def _release_input(
        self, prepared: RescrapeInput | None, *, dest_path: str | None, success: bool
    ) -> None:
        if prepared is None:
            return
        await self._resolver().release(prepared, dest_path=dest_path, success=success)

    async def restore_locators(self, record: HistoryRecord) -> dict:
        """从关联的 scrape_job 恢复 locator，用于重试/处理时定位 115 等云端文件。

        conflict_data 里没有保存 locator，但 scrape_jobs 表保留了完整的 locator。
        通过 record.scrape_job_id 查表恢复。
        """
        if not record.scrape_job_id:
            return {}
        try:
            job = await self._scrape_job_service.get_job(record.scrape_job_id)
            if job is None:
                return {}
            result = {
                "file_locator": job.file_locator,
                "output_locator": job.output_locator,
                "metadata_locator": job.metadata_locator,
                "allow_local_output": job.allow_local_output,
            }
            # 只保留非空值
            return {k: v for k, v in result.items() if v is not None and v is not False}
        except Exception:
            return {}

    async def execute_scrape_and_update(
        self,
        record_id: str,
        scrape_request,
        user_selection_log: str | None = None,
        *,
        resolve_input: bool = False,
    ) -> dict:
        """执行刮削并更新记录状态（公共逻辑）。

        resolve_input=True 是「重刮」入口：记录里的 folder_path 是 `源文件 => 产物`
        一整串，直接拿去当 file_path 必然报文件不存在，所以先按「源文件优先、没有源文件
        就用产物」解析出真实输入，并清掉上一轮旧产物（否则整理步骤会报目标文件已存在）。
        冲突处理分支不传这个参数：那里的产物属于别的记录，删不得。
        """
        from server.bootstrap import get_scraper_service

        history_service = self._history_service
        scraper = get_scraper_service()

        # 获取原有日志和 manual_job_id
        record = await history_service.get_record(record_id)
        existing_logs = list(record.scrape_logs) if record and record.scrape_logs else []
        manual_job_id = record.manual_job_id if record else None

        # 解析输入并发日志（在落 running 之前）：源文件与产物都没了要直接报 400，
        # 不能先把状态改成运行中再失败
        prepared: RescrapeInput | None = None
        registration_source: str | None = None
        if resolve_input and record is not None:
            remote = bool(getattr(scrape_request, "file_locator", None))
            await self.fill_organize_params(record, scrape_request)
            prepared, prepared_steps, registration_source = await self.prepare_input(record, remote=remote)
            scrape_request.file_path = prepared.path
            existing_logs.extend(prepared_steps)
            await history_service.update_scrape_logs(record_id, existing_logs)

        # 如果有用户选择日志，添加到原有日志后
        if user_selection_log:
            user_log = ScrapeLogStep(
                name="用户手动选择",
                completed=True,
                logs=[ScrapeLogEntry(message=user_selection_log)],
            )
            existing_logs.append(user_log)
            await history_service.update_scrape_logs(record_id, existing_logs)

        # 创建日志回调
        async def on_log_update(logs):
            # 将新日志追加到原有日志后
            combined_logs = existing_logs + logs
            await history_service.update_scrape_logs(record_id, combined_logs)

        # 先落 running：列表与详情页据此实时显示「正在处理」——重刮/处理都要跑几十秒；
        # started_at 取当下（记录可能创建于几天前，executed_at 不能当本次开始时间用）
        run_started_at = datetime.now()
        await history_service.update_record(
            record_id,
            status=TaskStatus.RUNNING,
            error_message="",
            started_at=run_started_at,
            # 记下本次使用的匹配：用户换过 ID/季/集时记录跟着更新，
            # 下次重试的预填不会又拿出旧 ID
            tmdb_id=scrape_request.tmdb_id,
            season_number=scrape_request.season,
            episode_number=scrape_request.episode,
        )

        try:
            result = await scraper.scrape_by_id(scrape_request, on_log_update=on_log_update)
        except HTTPException:
            await self._release_input(prepared, dest_path=None, success=False)
            raise
        except Exception as exc:
            # 任何未预期的异常都要落 failed，否则记录会永远停在 running，
            # 而 running 状态在本服务里是禁止再加工的
            await history_service.update_record(
                record_id,
                status=TaskStatus.FAILED,
                error_message=str(exc),
                duration_seconds=(datetime.now() - run_started_at).total_seconds(),
            )
            await self._release_input(prepared, dest_path=None, success=False)
            raise

        return await self._finalize_prepared(
            record_id=record_id,
            result=result,
            prepared=prepared,
            manual_job_id=manual_job_id,
            duration_seconds=(datetime.now() - run_started_at).total_seconds(),
            registration_source=registration_source,
        )

    async def reorganize_from_metadata(self, record_id: str) -> dict:
        """用已存元数据重跑整理（命名/移动/NFO/图片），全程不请求 TMDB。

        与重刮的分工：重新整理用于「配置改了、目标被删了、上次整理结果不对」；
        要拿 TMDB 的最新元数据则应该走重刮。
        """
        from server.bootstrap import get_scraper_service

        history_service = self._history_service

        record = await history_service.get_record(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")

        if record.status == TaskStatus.RUNNING:
            raise HTTPException(status_code=400, detail="该记录正在处理中，请稍后再试")

        season_num = record.season_number
        episode_num = record.episode_number
        if not record.title or season_num is None or episode_num is None:
            raise HTTPException(
                status_code=400,
                detail="记录缺少已存的标题或季集信息，请改用「按 TMDB ID 重刮」",
            )

        # 源文件路径与 TMDB ID 都取自登记表，缺失时退回记录自身
        files = await self._scraped_file_service.list_by_history_record(record_id)
        tmdb_id = next((item.tmdb_id for item in files if item.tmdb_id), None)
        if tmdb_id is None and record.conflict_data:
            tmdb_id = record.conflict_data.get("tmdb_id")
        if tmdb_id is None:
            raise HTTPException(
                status_code=400,
                detail="记录缺少 TMDB ID，请改用「按 TMDB ID 重刮」",
            )

        locators = await self.restore_locators(record)
        # 输入与重刮同一套规则：源文件优先、源文件没了就用产物（顺带清旧产物，
        # 否则整理步骤会撞上「目标文件已存在」）
        prepared, prepared_steps, registration_source = await self.prepare_input(
            record, remote=bool(locators.get("file_locator"))
        )

        series, season_info = rebuild_metadata(
            record, tmdb_id=tmdb_id, season_num=season_num, episode_num=episode_num
        )

        conflict_data = record.conflict_data or {}
        link_mode_value = conflict_data.get("link_mode")
        from server.models.organize import OrganizeMode

        scrape_request = ScrapeByIdRequest(
            file_path=prepared.path,
            tmdb_id=tmdb_id,
            season=season_num,
            episode=episode_num,
            output_dir=conflict_data.get("output_dir"),
            metadata_dir=conflict_data.get("metadata_dir"),
            link_mode=OrganizeMode(link_mode_value) if link_mode_value else None,
            **locators,
        )
        # 重新整理同样要补齐输出目录：缺了会改成「原地重命名」
        await self.fill_organize_params(record, scrape_request)

        existing_logs = list(record.scrape_logs or [])
        existing_logs.extend(prepared_steps)
        user_log = ScrapeLogStep(
            name="重新整理",
            completed=True,
            logs=[ScrapeLogEntry(message="用户触发重新整理（使用已存元数据）")],
        )
        existing_logs.append(user_log)
        await history_service.update_scrape_logs(record_id, existing_logs)

        # 先落 running 状态：列表与详情页据此实时显示「正在重新整理」
        run_started_at = datetime.now()
        await history_service.update_record(
            record_id,
            status=TaskStatus.RUNNING,
            error_message="",
            started_at=run_started_at,
        )

        async def on_log_update(logs):
            await history_service.update_scrape_logs(record_id, existing_logs + logs)

        scraper = get_scraper_service()
        try:
            result = await scraper.reorganize_with_metadata(
                scrape_request,
                series=series,
                season_info=season_info,
                on_log_update=on_log_update,
            )
        except Exception:
            await self._release_input(prepared, dest_path=None, success=False)
            raise

        return await self._finalize_prepared(
            record_id=record_id,
            result=result,
            prepared=prepared,
            manual_job_id=record.manual_job_id,
            duration_seconds=(datetime.now() - run_started_at).total_seconds(),
            registration_source=registration_source,
        )

    async def _finalize(
        self,
        record_id: str,
        result,
        *,
        manual_job_id: int | None,
        duration_seconds: float,
        folder_path: str | None = None,
        replace_registration: bool = False,
        registration_source: str | None = None,
    ) -> dict:
        """刮削与重新整理共用的收尾：写回状态与元数据、维护手动任务计数。

        两个入口共用同一段落，避免「重刮成功但重新整理漏更字段」这类悄悄漂移。
        folder_path 只在重跑（重刮/重新整理）时传入，写成本次实际的「输入 => 产物」；
        replace_registration 让登记就地更新原行，不因路径变化多出一条。
        """
        history_service = self._history_service
        history_service.clear_log_cache(record_id)

        if result.status.value == "success":
            series = result.series_info
            episode = result.episode_info
            await history_service.update_record(
                record_id,
                status=TaskStatus.SUCCESS,
                # 空串而不是 None：update_fields 把 None 解释成「字段不修改」，
                # 传 None 会让上一次失败的错误信息挂在「成功」记录上
                error_message="",
                title=series.name if series else None,
                original_title=series.original_name if series else None,
                plot=series.overview if series else None,
                poster_url=f"https://image.tmdb.org/t/p/w500{series.poster_path}" if series and series.poster_path else None,
                release_date=str(series.first_air_date) if series and series.first_air_date else None,
                rating=series.vote_average if series else None,
                tags=series.genres if series else None,
                season_number=result.parsed_season,
                episode_number=result.parsed_episode,
                episode_title=episode.name if episode else None,
                episode_overview=episode.overview if episode else None,
                episode_still_url=f"https://image.tmdb.org/t/p/w500{episode.still_path}" if episode and episode.still_path else None,
                episode_air_date=str(episode.air_date) if episode and episode.air_date else None,
                # 重刮/重新整理也要写回真实耗时（此前后端只在任务流里写，重刮记录永远是旧值）
                duration_seconds=duration_seconds,
                folder_path=folder_path,
            )

            # 登记产物：scraped_files 是记录详情「删除文件」与「重新整理」定位源文件/产物的依据
            # （设置页的「已刮削文件」面板已于 2026-09-27 移除，登记本身仍是必要的）。
            # 登记失败不影响刮削结果本身，故单独兜住异常。
            try:
                await self._scraped_file_service.register_output(
                    history_record_id=record_id,
                    # 重跑时用调用方给的源路径（产物作为输入时为空，保留登记行原有源文件）
                    source_path=(
                        registration_source if registration_source is not None else result.file_path
                    ),
                    target_path=result.dest_path,
                    tmdb_id=result.selected_id,
                    season=result.parsed_season,
                    episode=result.parsed_episode,
                    title=series.name if series else None,
                    replace_existing=replace_registration,
                )
            except Exception as exc:  # noqa: BLE001 - 登记是附带动作，不能拖垮刮削结果
                logger.warning(f"登记刮削产物失败 {record_id}: {exc}")

            # 手动任务统计以子记录为准：重试成功后按记录重算，
            # 而不是在这条路径上做 ±1 累加（旧写法会把成功数顶过总数）
            if manual_job_id:
                from server.application.manual_job_service import ManualJobService

                await ManualJobService().refresh_progress(manual_job_id)

            return {"success": True, "message": "处理成功", "dest_path": result.dest_path}

        await history_service.update_record(
            record_id,
            status=TaskStatus.FAILED,
            error_message=result.message,
            duration_seconds=duration_seconds,
        )
        raise HTTPException(status_code=400, detail=result.message)
