"""Parser plugins for filename parsing."""

from server.domain.parsing.parsers.base import ParseContext, ParserPlugin
from server.domain.parsing.parsers.cleaner import CleanerPlugin
from server.domain.parsing.parsers.episode_chinese import EpisodeChinesePlugin
from server.domain.parsing.parsers.episode_japanese import EpisodeJapanesePlugin
from server.domain.parsing.parsers.episode_standard import EpisodeStandardPlugin
from server.domain.parsing.parsers.series_name import SeriesNamePlugin

# 默认插件列表（按优先级排序）
DEFAULT_PLUGINS: list[type[ParserPlugin]] = [
    CleanerPlugin,
    EpisodeStandardPlugin,
    EpisodeJapanesePlugin,
    EpisodeChinesePlugin,
    SeriesNamePlugin,
]

__all__ = [
    "ParseContext",
    "ParserPlugin",
    "CleanerPlugin",
    "EpisodeStandardPlugin",
    "EpisodeJapanesePlugin",
    "EpisodeChinesePlugin",
    "SeriesNamePlugin",
    "DEFAULT_PLUGINS",
]
