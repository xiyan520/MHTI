"""Manual job data models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from server.models.storage import StorageLocator


class ManualJobStatus(str, Enum):
    """Manual job status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LinkMode(int, Enum):
    """File organization mode."""

    HARDLINK = 1
    MOVE = 2
    COPY = 3
    SYMLINK = 4


class JobSource(str, Enum):
    """任务来源类型"""

    MANUAL = "manual"  # 手动创建
    WATCHER = "watcher"  # 文件监控触发


class ManualJobAdvancedSettings(BaseModel):
    """手动任务高级设置 - 分类全局配置开关"""

    # 各分类的全局配置开关
    use_global_organize: bool = True
    use_global_download: bool = True
    use_global_naming: bool = True
    use_global_metadata: bool = True

    # 整理设置（当 use_global_organize=False 时使用）
    metadata_folder: str = ""
    file_size_filter: int = 100
    file_ext_whitelist: list[str] = []
    file_name_blacklist: list[str] = []
    file_sanitize_list: list[str] = []
    delete_metadata_on_fail: bool = False
    overwrite_video: bool = False
    overwrite_image: bool = False
    protect_ext_whitelist: bool = False
    delete_by_size: bool = False
    delete_by_ext: bool = False
    delete_by_name: bool = False
    extra_ext_whitelist: list[str] = []

    # 下载设置（当 use_global_download=False 时使用）
    download_poster: bool = True
    download_thumb: bool = True
    download_fanart: bool = False

    # 命名设置（当 use_global_naming=False 时使用）
    series_folder_template: str = ""
    season_folder_template: str = ""
    episode_file_template: str = ""

    # 元数据设置（当 use_global_metadata=False 时使用）
    scrape_title: bool = True
    scrape_plot: bool = True
    nfo_enabled: bool = True


class ManualJob(BaseModel):
    """Manual job record."""

    id: int
    scan_path: str
    target_folder: str
    metadata_dir: str = ""  # 元数据目录
    scan_locator: StorageLocator | None = None
    target_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: LinkMode
    delete_empty_parent: bool = True
    config_reuse_id: int | None = None
    source: JobSource = JobSource.MANUAL  # 任务来源
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: ManualJobStatus = ManualJobStatus.PENDING
    success_count: int = 0
    skip_count: int = 0
    error_count: int = 0
    total_count: int = 0
    # 投递阶段写入：本次已处理（投递成功 + 跳过）的文件数。为 None 表示扫描/
    # 投递尚未结束，任务此时还无法判定是否已处理完。
    dispatched_count: int | None = None
    error_message: str | None = None


class ManualJobCreate(BaseModel):
    """Request for creating a manual job."""

    scan_path: str
    target_folder: str
    metadata_dir: str = ""  # 元数据目录
    scan_locator: StorageLocator | None = None
    target_locator: StorageLocator | None = None
    metadata_locator: StorageLocator | None = None
    allow_local_output: bool = False
    link_mode: LinkMode = LinkMode.MOVE
    delete_empty_parent: bool = True
    config_reuse_id: int | None = None
    source: JobSource = JobSource.MANUAL  # 任务来源
    advanced_settings: ManualJobAdvancedSettings | None = None  # 高级设置


class ManualJobListResponse(BaseModel):
    """Response for listing manual jobs."""

    jobs: list[ManualJob]
    total: int


class ManualJobDeleteRequest(BaseModel):
    """Request for deleting manual jobs."""

    ids: list[int]
