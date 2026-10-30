"""Rename service for organizing video files."""

import errno
import logging
import os
import shutil
import uuid
from pathlib import Path

from server.domain.system.template_service import TemplateService
from server.models.organize import OrganizeMode
from server.models.rename import (
    BatchRenameRequest,
    BatchRenameResponse,
    RenamePreview,
    RenameRequest,
    RenameResult,
)

logger = logging.getLogger(__name__)


class RenameService:
    """Service for renaming and organizing video files."""

    def __init__(self, template_service: TemplateService | None = None) -> None:
        """Initialize the rename service."""
        self._template_service = template_service or TemplateService()

    def preview_rename(self, request: RenameRequest) -> RenamePreview:
        """Preview a rename operation without executing it.

        Args:
            request: Rename request with source and metadata.

        Returns:
            Preview of the rename operation.
        """
        source_path = Path(request.source_path)
        extension = source_path.suffix
        active_template = self._template_service.get_active_template()

        # Build template data
        data = self._build_template_data(request)

        # Generate new filename
        episode_template = active_template.episode_file
        new_filename = self._template_service.format_filename(episode_template, data)
        new_filename = self._template_service.sanitize_filename(new_filename)
        new_filename = f"{new_filename}{extension}"

        # Generate folder structure
        series_folder = self._template_service.format_filename(
            active_template.series_folder, data
        )
        series_folder = self._template_service.sanitize_filename(series_folder)
        # 如果没有年份，移除空括号
        series_folder = series_folder.replace(" ()", "")

        season_folder = self._template_service.format_filename(
            active_template.season_folder, data
        )
        season_folder = self._template_service.sanitize_filename(season_folder)

        # Determine output directory
        if request.output_dir:
            base_dir = Path(request.output_dir)
        else:
            base_dir = source_path.parent

        dest_folder = base_dir / series_folder / season_folder
        dest_path = dest_folder / new_filename

        # Determine which directories need to be created
        will_create_dirs = []
        check_dir = dest_folder
        while not check_dir.exists() and check_dir != base_dir.parent:
            will_create_dirs.insert(0, str(check_dir))
            check_dir = check_dir.parent

        return RenamePreview(
            source_path=str(source_path),
            dest_path=str(dest_path),
            dest_folder=str(dest_folder),
            new_filename=new_filename,
            will_create_dirs=will_create_dirs,
        )

    def execute_rename(
        self,
        request: RenameRequest,
        create_backup: bool = False,
    ) -> RenameResult:
        """Execute a rename operation.

        Args:
            request: Rename request with source and metadata.
            create_backup: Whether to create a backup of the original file.

        Returns:
            Result of the rename operation.
        """
        source_path = Path(request.source_path)

        logger.info(f"execute_rename: 源文件 = {source_path}")

        # Check source exists
        if not source_path.exists():
            logger.error(f"源文件不存在: {source_path}")
            return RenameResult(
                source_path=str(source_path),
                dest_path="",
                success=False,
                error=f"Source file not found: {source_path}",
            )

        # Get preview for paths
        preview = self.preview_rename(request)
        dest_path = Path(preview.dest_path)
        dest_folder = Path(preview.dest_folder)

        logger.info(f"execute_rename: 目标文件夹 = {dest_folder}")
        logger.info(f"execute_rename: 目标路径 = {dest_path}")

        try:
            # Create destination directory
            dest_folder.mkdir(parents=True, exist_ok=True)
            logger.info("execute_rename: 目录已创建/存在")

            # Create backup if requested
            backup_path = None
            if create_backup:
                backup_path = self._create_backup(source_path)

            # Check if destination already exists
            if dest_path.exists() and dest_path != source_path:
                logger.warning(f"目标文件已存在: {dest_path}")
                return RenameResult(
                    source_path=str(source_path),
                    dest_path=str(dest_path),
                    success=False,
                    error=f"Destination file already exists: {dest_path}",
                    backup_path=backup_path,
                )

            # Move/rename the file based on link_mode
            logger.info(f"execute_rename: 正在处理文件，模式: {request.link_mode or 'move(默认)'}...")
            self._execute_file_operation(source_path, dest_path, request.link_mode)
            logger.info("execute_rename: 文件处理成功!")

            return RenameResult(
                source_path=str(source_path),
                dest_path=str(dest_path),
                success=True,
                backup_path=backup_path,
            )

        except PermissionError as e:
            logger.error(f"权限错误: {e}")
            return RenameResult(
                source_path=str(source_path),
                dest_path=str(dest_path),
                success=False,
                error=f"Permission denied: {e}",
            )
        except OSError as e:
            logger.error(f"OS 错误: {e}")
            return RenameResult(
                source_path=str(source_path),
                dest_path=str(dest_path),
                success=False,
                error=f"OS error: {e}",
            )

    def batch_rename(self, request: BatchRenameRequest) -> BatchRenameResponse:
        """Execute batch rename operations.

        Args:
            request: Batch rename request with items and options.

        Returns:
            Batch rename response with all results.
        """
        results: list[RenameResult] = []
        previews: list[RenamePreview] | None = None

        if request.dry_run:
            previews = []
            for item in request.items:
                preview = self.preview_rename(item)
                previews.append(preview)
                # For dry run, create a "success" result without actually renaming
                results.append(
                    RenameResult(
                        source_path=preview.source_path,
                        dest_path=preview.dest_path,
                        success=True,
                    )
                )
        else:
            for item in request.items:
                result = self.execute_rename(item, create_backup=request.create_backup)
                results.append(result)

        success_count = sum(1 for r in results if r.success)
        failed_count = len(results) - success_count

        return BatchRenameResponse(
            total=len(results),
            success=success_count,
            failed=failed_count,
            results=results,
            previews=previews,
        )

    def _build_template_data(self, request: RenameRequest) -> dict:
        """Build template data dictionary from request.

        Args:
            request: Rename request.

        Returns:
            Dictionary with template variables.
        """
        data = {
            "title": request.title,
            "season": request.season,
            "episode": request.episode,
            "episode_title": request.episode_title or "",
            "year": request.year or "",
            "original_title": request.title,  # Default to title if not provided
            "air_date": "",
        }
        return data

    def _create_backup(self, source_path: Path) -> str:
        """Create a backup of the source file.

        Args:
            source_path: Path to the source file.

        Returns:
            Path to the backup file.
        """
        backup_path = source_path.with_suffix(source_path.suffix + ".bak")
        counter = 1
        while backup_path.exists():
            backup_path = source_path.with_suffix(f"{source_path.suffix}.bak{counter}")
            counter += 1

        shutil.copy2(str(source_path), str(backup_path))
        return str(backup_path)

    def _execute_file_operation(
        self,
        source_path: Path,
        dest_path: Path,
        link_mode: OrganizeMode | None,
    ) -> None:
        """根据整理模式执行文件操作。

        Args:
            source_path: 源文件路径
            dest_path: 目标文件路径
            link_mode: 整理模式（copy/move/hardlink/symlink）
        """
        mode = link_mode or OrganizeMode.MOVE  # 默认移动

        if mode == OrganizeMode.HARDLINK:
            os.link(str(source_path), str(dest_path))
            logger.info(f"硬链接已创建: {source_path} -> {dest_path}")
            return
        if mode == OrganizeMode.SYMLINK:
            os.symlink(str(source_path), str(dest_path))
            logger.info(f"软链接已创建: {source_path} -> {dest_path}")
            return

        # 复制/移动都先落到同目录临时文件再 os.replace 原子替换：
        # 中途失败（含被超时取消）不会在目标位留下半截视频，重试时也不会把
        # 「半个文件」当成已存在的成品。临时名不带视频扩展名，扫描侧不会误收。
        tmp_path = dest_path.with_name(f"{dest_path.name}.mhti-tmp-{uuid.uuid4().hex[:8]}")

        if mode == OrganizeMode.MOVE:
            try:
                # 同盘 rename 是原子操作，也省掉一次完整复制
                os.replace(str(source_path), str(dest_path))
                logger.info(f"文件已移动: {source_path} -> {dest_path}")
                return
            except OSError as e:
                if e.errno != errno.EXDEV:
                    raise
                # 跨盘：退化为「复制到临时文件 → 原子替换」，再删源

        try:
            shutil.copy2(str(source_path), str(tmp_path))
            os.replace(str(tmp_path), str(dest_path))
        except BaseException:
            # CancelledError 是 BaseException：超时取消同样要清掉半成品
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError as cleanup_error:
                logger.warning(f"清理临时文件失败: {tmp_path}: {cleanup_error}")
            raise

        if mode == OrganizeMode.COPY:
            logger.info(f"文件已复制: {source_path} -> {dest_path}")
            return

        # MOVE 的跨盘分支：目标已完整落地，才允许删源
        try:
            source_path.unlink()
        except OSError as e:
            # 宁可留一份重复文件，也不能让用户以为源被搬走却其实还在
            logger.error(f"跨盘移动已复制但删除源失败: {source_path}: {e}")
            raise
        logger.info(f"文件已移动(跨盘): {source_path} -> {dest_path}")

    def create_series_structure(
        self,
        output_dir: str,
        title: str,
        seasons: list[int] | None = None,
    ) -> list[str]:
        """Create the standard series folder structure.

        Args:
            output_dir: Base output directory.
            title: Series title.
            seasons: List of season numbers to create (optional).

        Returns:
            List of created directories.
        """
        created_dirs = []
        base_path = Path(output_dir)

        # Sanitize title
        safe_title = self._template_service.sanitize_filename(title)
        series_path = base_path / safe_title

        # Create series folder
        series_path.mkdir(parents=True, exist_ok=True)
        created_dirs.append(str(series_path))

        # Create season folders if specified
        if seasons:
            for season_num in seasons:
                season_folder = f"Season {season_num:02d}"
                season_path = series_path / season_folder
                season_path.mkdir(exist_ok=True)
                created_dirs.append(str(season_path))

        return created_dirs
