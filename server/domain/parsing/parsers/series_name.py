"""Series name extraction plugin - 剧名提取器.

仅从文件名提取，不依赖路径。

提取策略：
1. 基于集数标记定位剧名边界
2. 年份识别
3. 置信度计算
"""

import re

from server.domain.parsing.parsers.base import ParseContext, ParserPlugin
from server.domain.parsing.parsers.episode_japanese import KANJI_CHARS

# ============================================================================
# 集数标记模式（用于定位剧名结束位置）
# ============================================================================
EPISODE_MARKERS = [
    # ===== 标准格式 =====
    r"[Ss]\d{1,2}[.\s_-]?[Ee]\d{1,3}",        # S01E01, S01.E01, S01 E01
    r"[Ss]\d{1,2}(?=[.\s_-]|$)",               # S01 单独出现
    r"[Ee][Pp]?\d{1,3}",                       # EP01, E01

    # ===== 中文格式 =====
    r"第\d+[季集话回章弾話幕巻卷]",               # 第1季, 第1集, 第1話
    r"第[一二三四五六七八九十百]+[季集话回章弾話幕巻卷]",  # 第一季, 第一集
    r"第\s*\d{1,3}\s*[-~〜～ー]\s*\d{1,3}\s*[話话集回章弾幕]",  # 第1-2話（多集文件）
    # ===== 日语格式 =====
    r"前編|後編|前篇|後篇|上巻|下巻|中編|中篇",  # 前篇/后篇
    r"上集|下集|中集",                        # 上/下集
    rf"[其そ][のノ之乃][{KANJI_CHARS}\d]+",  # 其の一, その1
    r"[＃#♯]\d+",                            # #1

    # ===== 无障碍分隔裸数字（作品名 1、作品名 01）=====
    # 只认被空格/下划线/中点分隔开的 1-3 位数字，避免命中 1080p 这类 4 位噪点
    r"[\s_\-·](\d{1,3})(?=[\s_\-·]|$)",

    # ===== 其他标记 =====
    r"[Vv]ol\.?\s*\d+",                       # Vol.1
    r"巻\s*\d+",                              # 巻1
    r"Episode\s*\d+",                         # Episode 1
    r"Act\.?\s*\d+",                          # Act 1
    r"\[\d{1,3}\]",                           # [01]
    r"\(\d{1,2}\)\s*$",                       # (1) 在末尾
    r"[\s_\-·]([ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ])(?=[\s_\-·]|$)",  # 罗马数字集数：既已当集数，标题里要截掉

    # ===== 副标题标记 =====
    r"(?<=\s)～[^～]{2,}～",                 # ～副标题～（前面须有空格）
    r"(?<=\s)〜[^〜]{2,}〜",                 # 〜副标题〜
    r"「[^」]+」",                            # 「副标题」
    r"『[^』]+』",                            # 『副标题』

    # ===== 特别篇标记 =====
    # OVA/OAD/ONA/SP 要求字母边界：否则 SPY×FAMILY 这类标题会被整段截掉
    r"(?<![A-Za-z])(?:OVA|OAD|ONA|SP)(?![A-Za-z])|特別編|特別篇|番外編|番外篇",
    r"劇場版|剧场版|総集編|总集编|完結編|完结编|最終編|最终编",
]

# ============================================================================
# 年份模式
# ============================================================================
YEAR_PATTERN = r"[.\s_\(\[]?((?:19|20)\d{2})[.\s_\)\]]?"

# ============================================================================
# 需要移除的后缀
# ============================================================================
REMOVE_SUFFIXES = [
    r"\s*THE\s+ANIMATION\s*$",
    r"\s*the\s+animation\s*$",
    r"\s*-\s*The\s+Animation\s*$",
    r"\s*ANIMATION\s*$",
]

# ============================================================================
# 需要移除的前缀
# ============================================================================
REMOVE_PREFIXES = [
    r"^OVA\s+",
    r"^OAD\s+",
    r"^ONA\s+",
    r"^\[OVA\]\s*",
    r"^\[OAD\]\s*",
]


class SeriesNamePlugin(ParserPlugin):
    """剧名提取插件.

    解析优先级：50
    仅从文件名提取，不依赖路径。
    """

    priority = 50
    name = "series_name"

    def parse(self, ctx: ParseContext) -> ParseContext:
        # 从清洗后的文件名提取
        name = self._extract_from_cleaned(ctx.cleaned_filename)

        if name:
            ctx.series_name = name
            ctx.matched_patterns.append(f"{self.name}:extracted")

        # 提取年份
        year = self._extract_year(ctx.original_filename)
        if year:
            ctx.year = year

        # 计算置信度
        ctx.confidence = self._calculate_confidence(ctx)

        return ctx

    def _extract_from_cleaned(self, cleaned: str) -> str | None:
        """从清洗后的文件名提取剧名。"""
        text = cleaned

        # 找到最早的集数标记位置
        earliest_pos = len(text)

        for pattern in EPISODE_MARKERS:
            try:
                match = re.search(pattern, text)
                if match and match.start() < earliest_pos:
                    earliest_pos = match.start()
            except re.error:
                continue

        # 集数标记若被方括号单独包住（如 [作品名][第1話]），边界应落在方括号之前，
        # 否则会留下半截 "["，剧名被当成未闭合方括号组丢掉
        if earliest_pos > 0 and text[earliest_pos - 1] in "[（(【":
            earliest_pos -= 1

        # 检查年份位置
        year_match = re.search(YEAR_PATTERN, text)
        if year_match and year_match.start() < earliest_pos:
            earliest_pos = year_match.start()

        # 提取标记前的部分
        if earliest_pos > 0:
            name = text[:earliest_pos]
        else:
            name = text

        # 清理
        name = self._clean_name(name)

        if not name or len(name) < 2:
            return None

        # 纯数字/符号不是剧名（如 [01] 剥离外层括号后剩下的 "01"）
        if re.fullmatch(r"[\d\W_]+", name):
            return None

        return name

    def _clean_name(self, name: str) -> str:
        """清理剧名。"""
        # 规范化空白
        name = re.sub(r"\s+", " ", name)
        name = name.strip(" -_.")

        # 剥离外层方括号：[作品名] / 【作品名】 -> 作品名（可嵌套多层）
        for _ in range(3):
            wrapped = re.fullmatch(r"[\[\(（【]([^\]\)）】]+)[\]\)）】]", name)
            if not wrapped:
                break
            name = wrapped.group(1).strip()

        # 移除常见前缀
        for prefix in REMOVE_PREFIXES:
            name = re.sub(prefix, "", name, flags=re.I)

        # 移除常见后缀
        for suffix in REMOVE_SUFFIXES:
            name = re.sub(suffix, "", name, flags=re.I)

        # 移除末尾的连接符
        name = name.strip(" -_.")

        # 移除残留的方括号/括号（循环处理多个相邻方括号组）
        while True:
            stripped = re.sub(r"^\[[^\]]*\]\s*", "", name)
            stripped = re.sub(r"\s*\[[^\]]*\]$", "", stripped)
            if stripped == name:
                break
            name = stripped

        # 收尾：截断落在括号内时会留下未配对的括号，逐类配平
        for opener, closer in (("[", "]"), ("（", "）"), ("(", ")"), ("【", "】")):
            if name.count(opener) > name.count(closer):
                while name.startswith(opener) and name.count(opener) > name.count(closer):
                    name = name[1:].lstrip()
            elif name.count(closer) > name.count(opener):
                name = name.rstrip(closer + " ")

        return name.strip()

    def _extract_year(self, filename: str) -> int | None:
        """提取年份。"""
        match = re.search(YEAR_PATTERN, filename)
        if match:
            year = int(match.group(1))
            if 1950 <= year <= 2030:
                return year
        return None

    def _calculate_confidence(self, ctx: ParseContext) -> float:
        """计算置信度。

        评分标准：
        - 有剧名：+0.4
        - 有季数：+0.2
        - 有集数：+0.3
        - 有年份：+0.1
        """
        score = 0.0

        if ctx.series_name:
            score += 0.4
            if len(ctx.series_name) >= 4:
                score += 0.05

        if ctx.season is not None:
            score += 0.2

        if ctx.episode is not None:
            score += 0.3

        if ctx.year is not None:
            score += 0.1

        return min(score, 1.0)
