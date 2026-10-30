"""Standard episode pattern parser (S01E01, EP01, etc.) - 标准集数解析器.

文件名优先；季号缺失时从路径目录（如 ``Season 1``）补全。
"""

import re

from server.domain.parsing.parsers.base import ParseContext, ParserPlugin
from server.domain.parsing.parsers.episode_japanese import kanji_to_number

# 标准集数模式（按优先级排序）
STANDARD_PATTERNS = [
    # S01E01 或 S01.E01 格式
    (r"[.\s_-]?[Ss](\d{1,2})[.\s_-]?[Ee](\d{1,3})", "season_episode"),
    # EP01 或 E01 格式（仅集数；允许出现在文件名开头）
    (r"(?:^|[.\s_-])[Ee][Pp]?(\d{1,3})(?:[.\s_-]|$)", "episode_only"),
    # 集数范围（多集文件取首集）：1-2 / 01-02 / 第1-2話
    # 放在单集模式之后、末尾数字之前，避免范围被当成尾集
    (r"第\s*(\d{1,3})\s*[-~〜～ー]\s*\d{1,3}\s*[話话集回章弾幕]", "episode_range"),
    (r"[.\s_\[\(](\d{1,3})\s*[-~〜～ー]\s*\d{1,3}(?=[\s_\]\)]|$|[.．])", "episode_range"),
    # [01] 格式（仅集数）
    # 排除常见分辨率：720/480/576 是三位数，不加排除会变成「第 720 集」
    (r"\[(?!\s*(?:720|480|576)\s*\])(\d{1,3})\]", "episode_only"),
    # 末尾数字: - 01. 或 .01.
    (r"[.\s_-](\d{1,3})[.\s_-]?(?:\[|$|\.(?:mp4|mkv|avi))", "episode_only"),
    # 末尾数字 + 尾部元数据块（年份 / [1080p] / ～副标题～）
    # 例：作品名 01 (2023) [1080p].mp4、作品名 1 ～サブ～.mp4
    (
        r"[.\s_-](\d{1,3})"
        r"(?:\s*[\[\(（【][^\]\)）】]*[\]\)）】]"
        r"|\s*(?:19|20)\d{2}"
        r"|\s*[～〜][^～〜]*[～〜]"
        r"|\s*[.\s_-])*"
        r"[.\s_-]*(?:$|\.(?:mp4|mkv|avi|wmv|mov|flv|rmvb|ts|m2ts|webm|iso|m4v)$)",
        "episode_only",
    ),
    # 罗马数字集数：剧名 Ⅱ.mp4 / 剧名 Ⅳ [1080p].mp4
    # KANJI_NUMBERS 早就收了一整套罗马数字，缺的只是匹配规则；
    # 只认独立成段的一枚，并且后面只能是结尾或元数据块，避免把标题里的 Ⅱ 当集数
    (
        r"[\s._\-]([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ])"
        r"(?:\s*[\[\(（【][^\]\)）】]*[\]\)）】])?"
        r"[.\s_-]*(?:$|\.(?:mp4|mkv|avi|wmv|mov|flv|rmvb|ts|m2ts|webm|iso|m4v)$)",
        "episode_roman",
    ),
]

# 路径目录中的季号（如 .../Season 1/E01.mp4）
_PATH_SEASON_PATTERN = re.compile(r"[Ss]eason[.\s_-]*(\d{1,2})")


class EpisodeStandardPlugin(ParserPlugin):
    """标准集数格式解析插件.

    解析优先级：20
    文件名优先；仅当文件名已给出集数但未给出季号时，用路径目录补全季号。
    """

    priority = 20
    name = "episode_standard"

    def should_skip(self, ctx: ParseContext) -> bool:
        return ctx.episode is not None

    def parse(self, ctx: ParseContext) -> ParseContext:
        if self.should_skip(ctx):
            return ctx

        # 用清洗后的名字解析：日期前缀、制作组方括号、副标题已在 cleaner 里剥掉；
        # 继续读原文会让这些噪声参与匹配（例如画质标记 [720] 被当成第 720 集）
        text = ctx.cleaned_filename or ctx.original_filename
        for pattern, pattern_type in STANDARD_PATTERNS:
            match = re.search(pattern, text, re.I)
            if match:
                if pattern_type == "season_episode":
                    ctx.season = int(match.group(1))
                    ctx.episode = int(match.group(2))
                elif pattern_type in ("episode_only", "episode_range"):
                    # 范围集数（1-2）取首集，与 SxxE01-E02 命名惯例一致
                    ctx.episode = int(match.group(1))
                elif pattern_type == "episode_roman":
                    ctx.episode = kanji_to_number(match.group(1))

                if ctx.episode:
                    ctx.matched_patterns.append(f"{self.name}:{pattern_type}")
                    break

        # 路径补全季号：仅在已解析出集数、且文件名未给出季号时生效
        if ctx.episode is not None and ctx.season is None and ctx.filepath:
            match = _PATH_SEASON_PATTERN.search(ctx.filepath)
            if match:
                ctx.season = int(match.group(1))
                ctx.matched_patterns.append(f"{self.name}:path_season")

        return ctx
