"""Chinese episode pattern parser - 中文集数解析器.

仅解析文件名，不依赖路径。

支持格式：
- 第X季 第Y集
- 第X季 EY
- 第X集/话/回
- 中文数字
"""

import re

from server.domain.parsing.parsers.base import ParseContext, ParserPlugin
from server.domain.parsing.parsers.episode_japanese import kanji_to_number

# ============================================================================
# 中文集数模式（按优先级排序）
# ============================================================================
CHINESE_PATTERNS = [
    # ===== 季+集 组合格式 =====
    # 第X季 第Y集（允许中间有空格和其他字符）
    (r"第(\d{1,2})季.*?第(\d{1,3})[集话回巻卷]", "season_episode_digit"),
    # 第X季第Y集（紧密连接）
    (r"第(\d{1,2})季第(\d{1,3})[集话回巻卷]", "season_episode_digit"),
    # 第X季 EY 或 第X季 Y（空格或点分隔）
    (r"第(\d{1,2})季[.\s_-]+[Ee]?(\d{1,3})(?=[.\s_-]|$)", "season_episode_digit"),
    # 第X季EY（无分隔）
    (r"第(\d{1,2})季[Ee](\d{1,3})", "season_episode_digit"),

    # ===== 仅季数模式（用于后续集数匹配）=====
    (r"第(\d{1,2})季", "season_only"),

    # ===== 仅集数模式 =====
    # 第X集/话/回
    (r"第(\d+)[集话回巻卷]", "episode_digit"),
    # 第X話（日语写法但用数字）
    (r"第(\d+)話", "episode_digit"),
    # 中文数字
    (r"第([一二三四五六七八九十百千]+)[集话回話巻卷]", "episode_chinese"),
]


class EpisodeChinesePlugin(ParserPlugin):
    """中文集数格式解析插件.

    解析优先级：40
    仅从文件名解析，不依赖路径。
    """

    priority = 40
    name = "episode_chinese"

    def should_skip(self, ctx: ParseContext) -> bool:
        return ctx.episode is not None and ctx.season is not None

    def parse(self, ctx: ParseContext) -> ParseContext:
        # 与其它集数插件一致：清洗结果优先，原文兜底
        text = ctx.cleaned_filename or ctx.original_filename

        for pattern, pattern_type in CHINESE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                if pattern_type == "season_episode_digit":
                    if ctx.season is None:
                        ctx.season = int(match.group(1))
                    if ctx.episode is None:
                        ctx.episode = int(match.group(2))
                    ctx.matched_patterns.append(f"{self.name}:{pattern_type}")
                    return ctx

                elif pattern_type == "season_only":
                    if ctx.season is None:
                        ctx.season = int(match.group(1))
                        ctx.matched_patterns.append(f"{self.name}:season_only")
                    # 继续查找集数

                elif pattern_type == "episode_digit":
                    if ctx.episode is None:
                        ctx.episode = int(match.group(1))
                        ctx.matched_patterns.append(f"{self.name}:{pattern_type}")
                        return ctx

                elif pattern_type == "episode_chinese":
                    if ctx.episode is None:
                        ctx.episode = kanji_to_number(match.group(1))
                        if ctx.episode:
                            ctx.matched_patterns.append(f"{self.name}:{pattern_type}")
                            return ctx

        return ctx
