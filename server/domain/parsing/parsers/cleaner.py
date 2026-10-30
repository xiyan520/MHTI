"""Filename cleaner plugin - 里番专用文件名清洗器.

清洗策略：
1. 移除视频扩展名和语言标识
2. 移除日期前缀（如 [251114]、260703]、260703_、[2019-05-06]）
3. 移除制作组方括号
4. 移除末尾作者方括号
5. 移除 OVA/OAD/THE ANIMATION 标记
6. 移除副标题（～...～、「...」、集数后长文本）
"""

import re

from server.domain.parsing.parsers.base import ParseContext, ParserPlugin

# ============================================================================
# 视频文件扩展名
# ============================================================================
VIDEO_EXTENSIONS = r"\.(mp4|mkv|avi|wmv|mov|flv|rmvb|ts|m2ts|webm|iso|m4v)$"

# ============================================================================
# 语言标识（扩展名前）
# ============================================================================
LANGUAGE_SUFFIXES = [
    r"\.cht",   # 繁体中文
    r"\.chs",   # 简体中文
    r"\.chi",   # 中文
    r"\.tc",    # Traditional Chinese
    r"\.sc",    # Simplified Chinese
    r"\.jpn",   # 日语
    r"\.jap",   # 日语
    r"\.eng",   # 英语
    r"\.kor",   # 韩语
    r"\.zho",   # 中文 (ISO 639-3)
    r"\.und",   # 未定义
]

# ============================================================================
# 日期前缀：支持 [251114] / 251114] / 251114_ / [20191114] / [2019-05-06]
# 只认合法月日，避免把「6 位数字开头的剧名」误判成日期
# ============================================================================
DATE_TOKEN_PATTERN = re.compile(
    r"^\[?(?:(\d{2})(\d{2})(\d{2})|(\d{4})(\d{2})(\d{2})|(\d{4})[-_.](\d{2})[-_.](\d{2}))\]?"
)


def _is_valid_month_day(month: str, day: str) -> bool:
    """月 1-12、日 1-31。"""
    try:
        return 1 <= int(month) <= 12 and 1 <= int(day) <= 31
    except ValueError:
        return False


def strip_date_prefix(text: str) -> str:
    """移除开头的发布日期标记；不是合法日期则原样返回。

    发布名常见形式：[260703]剧名、260703]剧名、260703_剧名、[260703] 剧名。
    缺失左侧方括号（``260703]``）是实测遇到的真实文件名。
    """
    match = DATE_TOKEN_PATTERN.match(text)
    if not match:
        return text

    ymd = match.groups()
    if ymd[0] is not None:
        month, day = ymd[1], ymd[2]
    elif ymd[3] is not None:
        month, day = ymd[4], ymd[5]
    else:
        month, day = ymd[7], ymd[8]

    if not _is_valid_month_day(month, day):
        return text

    return text[match.end():]

# ============================================================================
# 里番制作组/发布者（扩展列表）
# ============================================================================
# 语言标记也可能是「_chs」「 chs」这种不带点的写法（…第1巻 chs.mp4）
TRAILING_LANGUAGE_TAG = re.compile(
    r"[\s_.\-]+(?:chs|cht|chi|sc|tc|jpn|jap|eng|kor|zho|und)$",
    re.I,
)

# ============================================================================
# 制作组 / 发行标签
# ============================================================================
# 这里写「规范名」而不是正则：名字内部的分隔符（空格、下划线、中黑、连字符）
# 由 _separator_tolerant 统一容错，免得每加一个组就要写两三个变体。
# 例：Queen_Bee村又… / Queen Bee 村又… / クイーン・ビー村又… 都能命中同一个条目。
_STUDIO_NAMES: tuple[str, ...] = (
    # ===== 主要里番制作会社 / 品牌 =====
    "Pink Pineapple",
    "ピンク・パイナップル",
    "Queen Bee",
    "クイーン・ビー",
    "King Bee",
    "Bunny Walker",
    "ばにぃうぉ～か～",
    "ばにぃうぉ〜か〜",
    "Mary Jane",
    "メリー・ジェーン",
    "ChiChinoya",
    "ちちのや",
    "Seven",
    "セブン",
    "Celeb",
    "セレブ",
    "Green Bunny",
    "グリーン・バニー",
    "Vanilla",
    "バニラ",
    "Animac",
    "アニマック",
    "Schoolzone",
    "スクールゾーン",
    "Majin",
    "魔人",
    "PoRO",
    "Shouten",
    "ショーテン",
    "Milky",
    "ミルキー",
    "Digital Works",
    "デジタル・ワークス",
    "DISCOVERY",
    "ディスカバリー",
    "GOLD BEAR",
    "Pashmina",
    "パシュミナ",
    "Lilith",
    "リリス",
    "BOOTLEG",
    "ぶーとれっぐ",
    "T-Rex",
    "ティーレックス",
    "Pixy",
    "ピクシー",
    "A1C",
    "Arms",
    "アームス",
    "ANIMATED",
    "アニメイテッド",
    "Collaboration Works",
    "コラボレーション・ワークス",
    "Studio Fantasia",
    "スタジオ・ファンタジア",
    "MS Pictures",
    "エム・エス・ピクチャーズ",
    "Suzuki Mirano",
    "鈴木みら乃",
    "Lune",
    "nur",
    "ヌール",
    "Marigold",
    "マリゴールド",
    "Breakbottle",
    # ===== 字幕组 / 搬运标签 =====
    "字幕组",
    "動畫瘋",
    "喵萌",
    "桜都字幕组",
    "樱都字幕组",
    "千夏字幕组",
    "幻樱字幕组",
    "极影字幕社",
    "动漫国字幕组",
    "诸神字幕组",
    "澄空学园",
    "LoliHouse",
)


def _separator_tolerant(name: str) -> str:
    """把规范名编译成正则：内部分隔符可缺省，且允许 空格/下划线/中黑/连字符 互换。"""
    parts = [re.escape(part) for part in re.split(r"[\s_\-・·.]+", name.strip()) if part]
    return r"[\s_\-・·.]*".join(parts)


def _studio_pattern(name: str) -> str:
    """补上字母边界：只在名字以拉丁字母结尾时要求后面不是字母。

    拉丁短名（nur）不加边界会把 nurse 之类英文词的开头当制作组；但日语名后面
    常常直接跟拉丁标题（魔人NTR騎士），那种情况不能拦。
    """
    pattern = _separator_tolerant(name)
    if re.search(r"[A-Za-z]$", name):
        pattern += r"(?![A-Za-z])"
    return pattern


# 长名优先，避免短名抢先命中
KNOWN_GROUPS: list[str] = [
    _studio_pattern(name)
    for name in sorted(_STUDIO_NAMES, key=len, reverse=True)
]

# ============================================================================
# 副标题模式（需要移除）
# ============================================================================
SUBTITLE_PATTERNS = [
    r"(?<=\s)～[^～]{2,}～",   # ～副标题～（前面须有空格，避免误删剧名中的波浪号）
    r"(?<=\s)〜[^〜]{2,}〜",   # 〜副标题〜
    r"「[^」]+」",             # 「副标题」
    r"『[^』]+』",             # 『副标题』
]

# ============================================================================
# 集数标记模式（用于定位副标题起始位置）
# ============================================================================
EPISODE_MARKERS_FOR_SUBTITLE = [
    r"第\s*[\d一二三四五六七八九十]+\s*[話话集回章弾幕]",  # 第1話, 第二話
    r"[＃#♯]\s*\d+",                                      # ＃2, #2
    r"[Vv]ol\.?\s*\d+",                                   # Vol.1
    r"前編|後編|前篇|後篇|上巻|下巻",                      # 前編/後編
    r"[其そ][のノ之乃]\s*[\d一二三四五六七八九十弍参肆伍]+",  # 其の弍, その1
]

# ============================================================================
# OVA/动画标记（需要移除）
# ============================================================================
ANIMATION_MARKERS = [
    r"\bOVA\b",
    r"\bOAD\b",
    r"\bONA\b",
    r"\bTHE\s+ANIMATION\b",
    r"\bANIMATION\b",
]


def has_meaningful_text(text: str) -> bool:
    """判断剥离集数标记与括号噪点后是否还剩实质内容（剧名）。

    用于判断开头方括号是否承载剧名：``[剧名] 第1話`` 里剥掉方括号后只剩集数标记，
    说明这个方括号本身是剧名，不能当作制作组删掉。
    """
    for pattern in EPISODE_MARKERS_FOR_SUBTITLE:
        text = re.sub(pattern, "", text)
    text = re.sub(r"[\[\]()（）【】「」『』\d\s._\-~～〜ー]+", "", text)
    return len(text) >= 2


def is_author_bracket(content: str) -> bool:
    """判断方括号内容是否为作者名。

    里番文件名中，作者名通常在末尾方括号内，格式为日文人名（2-8个字符）。
    """
    content = content.strip()

    # 日期格式 -> 不是作者
    if re.fullmatch(r"\d{6}|\d{8}", content):
        return False

    # 制作组 -> 不是作者（由其他逻辑处理）
    for group_pattern in KNOWN_GROUPS:
        if re.search(group_pattern, content, re.I):
            return False

    # 日文人名特征：2-8个字符，包含汉字/平假名/片假名
    if re.fullmatch(r"[\u4e00-\u9fa5\u3040-\u309f\u30a0-\u30ff]{2,8}", content):
        return True

    return False


def strip_known_group_prefix(text: str) -> str | None:
    """去掉开头没有被方括号包住的已知制作组名。

    制作组常常直接粘在剧名前面（魔人○○交配 / nurハメハラ /
    ピンクパイナップル無自覚な…），cleaner 只处理带方括号的形式，这些会原样留在
    剧名里，拿去 TMDB 搜是 0 结果。

    返回值：剥离后的文本；没命中、或剥离后不剩实质内容时返回 None。

    只给「搜索降级」这类场景用，清洗流程不要调用它——有些作品名本身就以制作组名
    开头（魔人探偵脳噛ネウロ），直接改写会毁掉正常标题。
    """
    stripped = text.lstrip()
    for pattern in KNOWN_GROUPS:
        match = re.match(pattern, stripped, re.I)
        if not match:
            continue
        rest = stripped[match.end():].lstrip(" -_.·:：")
        if has_meaningful_text(rest):
            return rest
    return None


class CleanerPlugin(ParserPlugin):
    """里番专用文件名清洗插件.

    清洗策略：
    - 移除日期、制作组、作者方括号
    - 移除 OVA/THE ANIMATION 标记
    - 移除副标题（保留集数标记）
    """

    priority = 10
    name = "cleaner"

    def parse(self, ctx: ParseContext) -> ParseContext:
        cleaned = ctx.original_filename

        # 1. 移除视频扩展名
        cleaned = re.sub(VIDEO_EXTENSIONS, "", cleaned, flags=re.I)

        # 2. 移除语言标识（.chs 这类带点写法）
        for pattern in LANGUAGE_SUFFIXES:
            cleaned = re.sub(pattern, "", cleaned, flags=re.I)

        # 2.5 再兜一层不带点的写法：作品名_chs / 作品名 chs
        cleaned = TRAILING_LANGUAGE_TAG.sub("", cleaned)

        # 3. 移除日期前缀 [251114] / 251114] / 251114_ / [2019-05-06]
        cleaned = strip_date_prefix(cleaned).lstrip()
        cleaned = cleaned.lstrip("-_.")

        # 4. 移除制作组和作者方括号
        cleaned = self._remove_group_and_author_brackets(cleaned)

        # 5. 移除 OVA/THE ANIMATION 标记
        for pattern in ANIMATION_MARKERS:
            cleaned = re.sub(pattern, " ", cleaned, flags=re.I)

        # 6. 移除副标题（～...～、「...」等）
        for pattern in SUBTITLE_PATTERNS:
            cleaned = re.sub(pattern, " ", cleaned)

        # 7. 移除集数后的副标题文本
        cleaned = self._remove_post_episode_subtitle(cleaned)

        # 8. 规范化空白和分隔符
        cleaned = re.sub(r"[._]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = cleaned.strip(" -")

        ctx.cleaned_filename = cleaned
        ctx.matched_patterns.append(f"{self.name}:cleaned")

        return ctx

    def _remove_group_and_author_brackets(self, text: str) -> str:
        """移除制作组和作者方括号。"""
        # 移除开头的制作组方括号（日期已移除，第一个方括号通常是制作组）。
        # 例外：括号内已含集数标记（[作品名 第1話]），或剥掉后只剩集数标记（[剧名] 第1話），
        # 都说明该方括号承载剧名而非制作组，此时保留。
        head = re.match(r"^\[([^\]]+)\](.*)$", text, re.S)
        if head and self._is_group_bracket(head.group(1), head.group(2)):
            text = head.group(2).lstrip()

        # 移除末尾的作者方括号
        match = re.search(r"\[([^\]]+)\]$", text)
        if match and is_author_bracket(match.group(1)):
            text = text[:match.start()]

        return text

    @staticmethod
    def _is_group_bracket(content: str, rest: str) -> bool:
        """开头方括号是否为制作组/画质标记（可安全删除）。"""
        if any(re.search(pattern, content) for pattern in EPISODE_MARKERS_FOR_SUBTITLE):
            return False
        return has_meaningful_text(rest)

    def _remove_post_episode_subtitle(self, text: str) -> str:
        """移除集数标记后的副标题文本。

        例如：
        - "勇者姫ミリア 第四話 砂漠の町のオークション！" -> "勇者姫ミリア 第四話"
        - "ながちち永井さん Vol.1 むちむちダイエット奮戦記" -> "ながちち永井さん Vol.1"
        """
        # 找到集数标记的位置
        for pattern in EPISODE_MARKERS_FOR_SUBTITLE:
            match = re.search(pattern, text)
            if match:
                # 集数标记结束位置（位于未闭合方括号内时顺延到闭合符之后）
                ep_end = self._extend_past_open_bracket(text, match)
                # 检查集数后是否有副标题（非空白内容）
                remaining = text[ep_end:].strip()
                if remaining:
                    # 有副标题，截断到集数标记结束
                    return text[:ep_end].strip()

        return text

    @staticmethod
    def _extend_past_open_bracket(text: str, match: re.Match) -> int:
        """集数标记处在未闭合的方括号内时，把截断点移到闭合符之后。

        例：``[作品名][第1話]`` 若不顺延会截成 ``[作品名][第1話``，
        残存的半个括号会让剧名提取把整条名字当成方括号组丢掉。
        """
        if text.count("[", 0, match.end()) <= text.count("]", 0, match.end()):
            return match.end()

        close = text.find("]", match.end())
        return close + 1 if close != -1 else match.end()
