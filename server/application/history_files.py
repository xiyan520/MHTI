"""历史记录的关联文件：清单与物理删除。

用户明确选择「物理删除 + 二次确认」，所以这里不做回收站，安全边界只能写在代码里：

- **只删文件，绝不删目录**：目录一律跳过并回报原因，因此不存在 rmtree 误删整棵目录树；
- **只删登记过的路径**：来源是 scraped_files 的 source_path / target_path。老记录没有登记行时，
  从记录 folder_path 里取「源文件 => 产物」两侧的路径（刮削成功都会这么写），
  只有 folder_path 本身就是一个文件时才把它当作源文件；
- **刮削产物包含元数据**：视频之外还有元数据目录里的 nfo 与图片（集封面 / season.nfo /
tvshow.nfo / poster.jpg / backdrop.jpg）。集专属的总是列出；**共享文件（season.nfo 与系列级的
tvshow.nfo、poster、backdrop）只在同季/同系列已经没有其它集时才列出**，
  否则删一集会把别的集要用的元数据一起带走（判定见 _shared_metadata_entries）；
- **路径必须落在允许的根目录内**：整理目录 / 元数据目录 / 记录里源文件与产物各自所在目录，
  且不能等于根目录本身。越界一律拒绝——这是对库里脏数据（或被手改过的路径）的兜底；
- **逐个文件回报结果**，不做整体回滚：已经删掉的文件不可能「回滚」回来，
  伪装的原子性只会让用户以为失败的操作没生效。
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.application.history_service import HistoryService, split_record_folder_path
from server.application.manual_job_service import ManualJobService
from server.application.scrape_job_service import ScrapeJobService
from server.application.scraped_file_service import ScrapedFileService
from server.domain.system.config_service import ConfigService
from server.models.history import (
    HistoryFileDeleteResponse,
    HistoryFileDeleteResult,
    HistoryFileEntry,
    HistoryFileListResponse,
    HistoryFileRole,
)

logger = logging.getLogger(__name__)

# 删除范围
_SCOPE_SOURCE = "source"
_SCOPE_ORGANIZED = "organized"
_SCOPE_ALL = "all"

# 「刮削产物」包含的角色：整理后的视频 + 元数据（nfo / 图片）
_ORGANIZED_ROLES = {HistoryFileRole.ORGANIZED, HistoryFileRole.METADATA}

# 元数据目录里与记录相关的文件后缀
_METADATA_SUFFIXES = {".nfo", ".jpg", ".jpeg", ".png", ".webp"}

# 目录扫描时忽略的系统垃圾文件（否则会误判为「同目录还有其他集」）
_JUNK_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

# 系列级共享元数据文件名
_SERIES_SHARED_NAMES = {"tvshow.nfo", "poster.jpg", "backdrop.jpg"}
_SEASON_SHARED_NAME = "season.nfo"


class HistoryFileService:
    """记录关联文件的清单与物理删除。"""

    def __init__(
        self,
        history_service: HistoryService,
        scraped_file_service: ScrapedFileService,
        config_service: ConfigService,
        scrape_job_service: ScrapeJobService | None = None,
        manual_job_service: ManualJobService | None = None,
    ) -> None:
        self._history_service = history_service
        self._scraped_file_service = scraped_file_service
        self._config_service = config_service
        # 任务服务用于查本次刮削真正用的元数据目录（全局配置往往是空的，
        # 目录实际随创建任务时填的参数走，存在 job 行上）
        self._scrape_job_service = scrape_job_service
        self._manual_job_service = manual_job_service

    # ---- 清单 ----

    async def list_files(self, record_id: str) -> HistoryFileListResponse:
        """列出记录关联的源文件与刮削产物（含元数据）。"""
        record = await self._history_service.get_record(record_id)
        if record is None:
            raise LookupError("记录不存在")

        config = await self._config_service.get_organize_config()
        conflict_data = record.conflict_data or {}
        metadata_root = await self._metadata_root(record, conflict_data, config.metadata_dir)
        roots = await self._allowed_roots(record.folder_path, metadata_root)
        entries: list[HistoryFileEntry] = []
        seen: set[str] = set()

        registered = await self._scraped_file_service.list_by_history_record(record_id)
        for item in registered:
            self._append(entries, seen, item.source_path, HistoryFileRole.SOURCE, roots)
            if item.target_path:
                self._append(entries, seen, item.target_path, HistoryFileRole.ORGANIZED, roots)

        # 没有登记行的老记录（登记功能上线前刮削的都属于这种）：从 folder_path 里
        # 把「源文件 => 产物」两侧拆出来，否则这类记录永远列不出任何文件，用户无从删除。
        if not entries:
            source_path, target_path = split_record_folder_path(record.folder_path)
            if target_path or source_path:
                # 没有产物侧时，folder_path 可能本身就是一个文件（也可能是目录，
                # 由 _describe 判定为不可删并说明原因）
                self._append(entries, seen, source_path, HistoryFileRole.SOURCE, roots)
                if target_path:
                    self._append(entries, seen, target_path, HistoryFileRole.ORGANIZED, roots)

        # 元数据（nfo / 图片）：产物视频所在系列与季的目录名在元数据目录下是同构的
        target_path = next(
            (entry.path for entry in entries if entry.role == HistoryFileRole.ORGANIZED), None
        )
        if target_path and metadata_root:
            for path in self._metadata_files(Path(target_path), Path(metadata_root)):
                self._append(entries, seen, str(path), HistoryFileRole.METADATA, roots)

        return HistoryFileListResponse(
            record_id=record_id,
            folder_path=record.folder_path,
            files=entries,
        )

    # ---- 元数据发现 ----

    async def _metadata_root(self, record, conflict_data: dict, config_dir: str | None) -> str | None:
        """找出记录对应的元数据根目录。

        优先级：记录里存的（重刮/重新整理时写回） > 本次运行的任务行（扫描流程创建任务时
        填的 metadata_dir） > 全局配置。实测全局配置可以是空的（用户只在创建任务时选了
        元数据目录），只看配置会一条元数据都找不到。
        """
        if conflict_data.get("metadata_dir"):
            return conflict_data["metadata_dir"]

        for service, job_id in (
            (self._scrape_job_service, record.scrape_job_id),
            (self._manual_job_service, record.manual_job_id),
        ):
            if service is None or not job_id:
                continue
            try:
                job = await service.get_job(job_id)
            except Exception as exc:  # noqa: BLE001 - 旧数据 / 外部存储异常不该让清单整个失败
                logger.warning("读取任务元数据目录失败 %s: %s", job_id, exc)
                continue
            metadata_dir = getattr(job, "metadata_dir", None)
            if metadata_dir:
                return metadata_dir

        return config_dir

    def _metadata_files(self, target: Path, metadata_root: Path) -> list[Path]:
        """列出产物视频对应的元数据文件（集专属总是列出，共享文件按需）。

        目录同构关系来自整理模板：产物 `<整理目录>\\<系列>\\<季>\\<stem>.mp4` 对应元数据
        `<元数据目录>\\<系列>\\<季>\\<stem>.nfo`，故只需拿产物路径的两层目录名去拼。
        """
        series_name = target.parent.parent.name
        season_name = target.parent.name
        if not series_name or not season_name:
            return []

        series_dir = metadata_root / series_name
        season_dir = series_dir / season_name
        if not season_dir.is_dir():
            return []

        # 集专属：文件名以视频名打头（覆盖 <stem>.nfo / <stem>.jpg / <stem>-thumb.jpg 等）
        episode_files = sorted(
            path
            for path in season_dir.iterdir()
            if path.is_file()
            and path.name.startswith(target.stem)
            and path.suffix.lower() in _METADATA_SUFFIXES
        )

        files = list(episode_files)
        if self._is_single_episode_season(target, episode_files, season_dir):
            season_shared = season_dir / _SEASON_SHARED_NAME
            if season_shared.is_file():
                files.append(season_shared)
        if self._is_single_episode_series(target, series_dir):
            files.extend(
                series_dir / name
                for name in sorted(_SERIES_SHARED_NAMES)
                if (series_dir / name).is_file()
            )
        return files

    def _is_single_episode_season(
        self, target: Path, episode_files: list[Path], metadata_season_dir: Path
    ) -> bool:
        """本季是否只剩这一集（决定 season.nfo 能否一起删）。

        产物侧与元数据侧都要看：视频可能在别处，但元数据目录里还留着别的集的 nfo/图片。
        """
        if self._has_other_files(target.parent, {target.name}, recursive=False):
            return False
        metadata_own = {path.name for path in episode_files} | {_SEASON_SHARED_NAME}
        return not self._has_other_files(metadata_season_dir, metadata_own, recursive=False)

    def _is_single_episode_series(self, target: Path, metadata_series_dir: Path) -> bool:
        """本系列是否只剩这一集（决定 tvshow.nfo / poster / backdrop 能否一起删）。"""
        # 产物侧：整个系列目录下只剩这一个视频
        if self._has_other_files(target.parent.parent, {target.name}, recursive=True):
            return False
        # 元数据侧：系列目录下只剩本集元数据与几份共享文件
        metadata_season_names = {
            path.name
            for season in metadata_series_dir.iterdir()
            if season.is_dir()
            for path in season.iterdir()
            if path.is_file() and path.name.startswith(target.stem)
        }
        skip = _SERIES_SHARED_NAMES | {_SEASON_SHARED_NAME} | metadata_season_names
        return not self._has_other_files(metadata_series_dir, skip, recursive=True)

    def _has_other_files(self, folder: Path, ignore_names: set[str], *, recursive: bool) -> bool:
        """目录里除 ignore_names 与系统垃圾文件外，是否还有别的文件。"""
        if not folder.is_dir():
            return False
        walker = folder.rglob("*") if recursive else folder.iterdir()
        for path in walker:
            if not path.is_file() or path.name in _JUNK_NAMES or path.name in ignore_names:
                continue
            return True
        return False

    def _append(
        self,
        entries: list[HistoryFileEntry],
        seen: set[str],
        raw_path: str | None,
        role: HistoryFileRole,
        roots: list[Path],
    ) -> None:
        """把单个路径转成条目（去重 + 判定可删性）。"""
        if not raw_path:
            return
        normalized = str(Path(raw_path))
        key = f"{role.value}:{normalized}"
        if key in seen:
            return
        seen.add(key)
        entries.append(self._describe(normalized, role, roots))

    def _describe(self, path_str: str, role: HistoryFileRole, roots: list[Path]) -> HistoryFileEntry:
        """判定单个文件是否可删，并把原因写给用户看。"""
        path = Path(path_str)

        if not path.exists():
            return HistoryFileEntry(
                path=path_str, role=role, exists=False, deletable=False, reason="文件不存在",
            )
        if path.is_dir():
            return HistoryFileEntry(
                path=path_str, role=role, exists=True, deletable=False,
                reason="目录不会被删除",
            )

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        if not roots:
            return HistoryFileEntry(
                path=path_str, role=role, exists=True, size=size, deletable=False,
                reason="未配置整理目录，无法校验路径",
            )
        for root in roots:
            if path == root:
                break
            if path.is_relative_to(root):
                return HistoryFileEntry(
                    path=path_str, role=role, exists=True, size=size, deletable=True,
                )
        return HistoryFileEntry(
            path=path_str, role=role, exists=True, size=size, deletable=False,
            reason="不在允许的目录范围内",
        )

    async def _allowed_roots(self, folder_path: str, metadata_dir: str | None = None) -> list[Path]:
        """允许删除的根目录：整理目录、元数据目录，以及记录里两个文件各自所在的目录。"""
        roots: list[Path] = []

        config = await self._config_service.get_organize_config()
        for raw in (config.organize_dir, config.metadata_dir, metadata_dir):
            if raw:
                roots.append(Path(raw))

        # 记录指向文件时，父目录即源目录；指向目录时它自己就是源目录
        for raw in split_record_folder_path(folder_path):
            if not raw:
                continue
            path = Path(raw)
            roots.append(path.parent if path.is_file() else path)

        return [r for r in roots if str(r) not in ("", ".")]

    # ---- 删除 ----

    async def delete_files(
        self,
        record_id: str,
        scope: str,
        *,
        keep_paths: set[str] | None = None,
        drop_registrations: bool = True,
    ) -> HistoryFileDeleteResponse:
        """按范围物理删除文件，并清掉对应的登记行。

        keep_paths 用于重刮：输入文件本身就取自产物时不能删；此时登记行也不能
        删（drop_registrations=False），否则重刮收尾只能新增一行，违背
        「登记不新开一条」的约定。
        """
        listing = await self.list_files(record_id)
        roles = {
            _SCOPE_SOURCE: {HistoryFileRole.SOURCE},
            _SCOPE_ORGANIZED: _ORGANIZED_ROLES,
            _SCOPE_ALL: {HistoryFileRole.SOURCE} | _ORGANIZED_ROLES,
        }[scope]
        keep = {str(Path(path)) for path in (keep_paths or set())}

        results: list[HistoryFileDeleteResult] = []
        removed_paths: list[str] = []

        for entry in listing.files:
            if entry.role not in roles:
                continue
            if str(Path(entry.path)) in keep:
                continue
            if not entry.deletable:
                results.append(
                    HistoryFileDeleteResult(
                        path=entry.path, role=entry.role, deleted=False,
                        reason=entry.reason or "不可删除",
                    )
                )
                continue
            try:
                Path(entry.path).unlink()
            except OSError as exc:  # 权限、占用、路径失效等
                logger.warning("删除文件失败 %s: %s", entry.path, exc)
                results.append(
                    HistoryFileDeleteResult(
                        path=entry.path, role=entry.role, deleted=False,
                        reason=f"删除失败: {exc}",
                    )
                )
                continue

            results.append(
                HistoryFileDeleteResult(path=entry.path, role=entry.role, deleted=True)
            )
            removed_paths.append(entry.path)

        # 登记行随文件一起清掉：产物或源文件没了，这条「已刮削」记录就不该继续存在，
        # 否则同一个文件再扫到时会被当成已处理而跳过。
        if removed_paths and drop_registrations:
            await self._scraped_file_service.delete_by_any_paths(removed_paths)

        deleted = sum(1 for r in results if r.deleted)
        failed = len(results) - deleted
        message = f"已删除 {deleted} 个文件" if deleted else "没有文件被删除"
        if failed:
            message += f"，{failed} 个未删除"

        return HistoryFileDeleteResponse(
            success=failed == 0 and deleted > 0,
            deleted=deleted,
            failed=failed,
            results=results,
            message=message,
            # 物理删除在磁盘上不可逆，撤销只覆盖记录行（见 UndoStore 说明）
            undoable=False,
        )

    async def clear_products(
        self, record_id: str, *, keep_paths: set[str] | None = None
    ) -> HistoryFileDeleteResponse:
        """重刮/重新整理前的旧产物清理：只删产物与元数据，保留输入文件与登记行。"""
        return await self.delete_files(
            record_id,
            _SCOPE_ORGANIZED,
            keep_paths=keep_paths,
            drop_registrations=False,
        )
