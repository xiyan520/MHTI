"""Organize configuration data models."""

from enum import Enum

from pydantic import BaseModel


class OrganizeMode(str, Enum):
    """整理模式枚举"""

    COPY = "copy"  # 复制模式
    MOVE = "move"  # 移动模式
    HARDLINK = "hardlink"  # 硬链接模式
    SYMLINK = "symlink"  # 软链接模式


class OrganizeConfig(BaseModel):
    """整理配置模型（同时用于请求和响应）"""

    organize_dir: str = ""  # 整理目录
    metadata_dir: str = ""  # 元数据目录
    organize_mode: OrganizeMode = OrganizeMode.COPY  # 整理模式
    min_file_size_mb: int = 100  # 文件大小过滤(MB)
    file_type_whitelist: list[str] = ["mkv", "mp4", "avi", "wmv", "ts", "rmvb"]  # 文件类型白名单
    filename_blacklist: list[str] = ["sample", "trailer"]  # 文件名黑名单
    junk_pattern_filter: list[str] = []  # 垃圾信息过滤正则
    auto_clean_source: bool = False  # 自动清理源目录
