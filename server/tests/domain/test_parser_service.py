"""Unit tests for ParserService."""

import pytest

from server.domain.parsing.parser_service import ParserService
from server.domain.parsing.parsers.cleaner import _STUDIO_NAMES, strip_known_group_prefix


@pytest.fixture
def parser_service():
    """Provide a ParserService instance for testing."""
    return ParserService()


class TestParserService:
    """Tests for ParserService class."""

    # Test S01E01 format
    @pytest.mark.parametrize(
        "filename,expected_name,expected_season,expected_episode",
        [
            ("Breaking.Bad.S01E01.720p.BluRay.mp4", "Breaking Bad", 1, 1),
            ("Breaking.Bad.S01.E01.720p.BluRay.mp4", "Breaking Bad", 1, 1),
            ("Game.of.Thrones.S08E06.1080p.mp4", "Game of Thrones", 8, 6),
            ("The.Office.US.S02E03.HDTV.x264.mp4", "The Office US", 2, 3),
        ],
    )
    def test_parse_s01e01_format(
        self, parser_service, filename, expected_name, expected_season, expected_episode
    ):
        """Test parsing S01E01 format filenames."""
        result = parser_service.parse(filename)

        assert result.series_name == expected_name
        assert result.season == expected_season
        assert result.episode == expected_episode
        assert result.is_parsed is True

    # Test with title format
    def test_parse_with_episode_title(self, parser_service):
        """Test parsing filename with episode title."""
        filename = "Game of Thrones - S01E01 - Winter Is Coming.mp4"
        result = parser_service.parse(filename)

        assert result.series_name == "Game of Thrones"
        assert result.season == 1
        assert result.episode == 1
        assert result.is_parsed is True

    # Test Chinese format
    @pytest.mark.parametrize(
        "filename,expected_name,expected_season,expected_episode",
        [
            ("绝命毒师 第1季 第01集.mp4", "绝命毒师", 1, 1),
            ("进击的巨人 第4季 第28集.mp4", "进击的巨人", 4, 28),
            ("权力的游戏 第8季 第6集.mp4", "权力的游戏", 8, 6),
        ],
    )
    def test_parse_chinese_format(
        self, parser_service, filename, expected_name, expected_season, expected_episode
    ):
        """Test parsing Chinese format filenames."""
        result = parser_service.parse(filename)

        assert result.series_name == expected_name
        assert result.season == expected_season
        assert result.episode == expected_episode
        assert result.is_parsed is True

    # Test bracket format
    def test_parse_bracket_format(self, parser_service):
        """Test parsing [XX] episode format."""
        filename = "[字幕组] 进击的巨人 [01].mp4"
        result = parser_service.parse(filename)

        assert result.series_name == "进击的巨人"
        assert result.episode == 1
        assert result.is_parsed is True

    # NOTE: Path parsing tests removed - architecture focuses on filename parsing only
    # See: "当前架构不可能使用路径来解析对应季/集，所以专注与文件名解析清洗"

    # Test year extraction
    def test_parse_year(self, parser_service):
        """Test year extraction from filename."""
        filename = "Breaking.Bad.2008.S01E01.mp4"
        result = parser_service.parse(filename)

        assert result.year == 2008
        assert result.series_name == "Breaking Bad"

    # Test cleaning patterns
    @pytest.mark.parametrize(
        "filename,expected_name",
        [
            ("Show.Name.S01E01.1080p.BluRay.x264.mp4", "Show Name"),
            ("Show.Name.S01E01.720p.HDTV.HEVC.mp4", "Show Name"),
            ("Show.Name.S01E01.4K.HDR.DTS.mp4", "Show Name"),
            ("Show.Name.S01E01.WEB-DL.AAC.mp4", "Show Name"),
        ],
    )
    def test_clean_technical_info(self, parser_service, filename, expected_name):
        """Test that technical info is cleaned from series name."""
        result = parser_service.parse(filename)

        assert result.series_name == expected_name

    # Test unparseable filename
    def test_parse_unparseable(self, parser_service):
        """Test handling of unparseable filename."""
        filename = "random_video_file.mp4"
        result = parser_service.parse(filename)

        assert result.original_filename == filename
        assert result.is_parsed is False or result.confidence < 0.5

    # Test batch parsing
    def test_parse_batch(self, parser_service):
        """Test batch parsing functionality."""
        files = [
            ("Breaking.Bad.S01E01.mp4", None),
            ("Game.of.Thrones.S01E01.mp4", None),
            ("random_file.mp4", None),
        ]
        results, success_rate = parser_service.parse_batch(files)

        assert len(results) == 3
        assert results[0].is_parsed is True
        assert results[1].is_parsed is True
        assert success_rate >= 0.66  # At least 2 out of 3

    # Test confidence calculation
    def test_confidence_full(self, parser_service):
        """Test high confidence with full info."""
        filename = "Breaking.Bad.2008.S01E01.mp4"
        result = parser_service.parse(filename)

        assert result.confidence >= 0.9

    def test_confidence_partial(self, parser_service):
        """Test lower confidence with partial info."""
        filename = "[01].mp4"
        result = parser_service.parse(filename)

        assert result.confidence < result.confidence if result.series_name else True

    # Test EP format
    def test_parse_ep_format(self, parser_service):
        """Test parsing EP01 format."""
        filename = "Show.Name.EP01.mp4"
        result = parser_service.parse(filename)

        assert result.episode == 1
        assert result.is_parsed is True

    # Test empty batch
    def test_parse_batch_empty(self, parser_service):
        """Test batch parsing with empty list."""
        results, success_rate = parser_service.parse_batch([])

        assert results == []
        assert success_rate == 0.0


class TestJapaneseEpisodeParser:
    """Tests for Japanese episode format parsing - 日语格式解析测试."""

    @pytest.fixture
    def parser_service(self):
        """Provide a ParserService instance for testing."""
        return ParserService()

    # ===== 其の系列测试 =====
    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            # 其の + 标准汉字数字
            ("好色の忠義くノ一ぼたん 其の一.mp4", 1),
            ("好色の忠義くノ一ぼたん 其の二.mp4", 2),
            ("好色の忠義くノ一ぼたん 其の三.mp4", 3),
            ("好色の忠義くノ一ぼたん 其の十.mp4", 10),
            ("好色の忠義くノ一ぼたん 其の十二.mp4", 12),
            # 其の + 日语大写数字
            ("好色の忠義くノ一ぼたん 其の壱.mp4", 1),
            ("好色の忠義くノ一ぼたん 其の弐.mp4", 2),
            ("好色の忠義くノ一ぼたん 其の弍.mp4", 2),  # 变体字形
            ("好色の忠義くノ一ぼたん 其の参.mp4", 3),
            ("好色の忠義くノ一ぼたん 其の弎.mp4", 3),  # 变体字形
            # 其の + 阿拉伯数字
            ("好色の忠義くノ一ぼたん 其の1.mp4", 1),
            ("好色の忠義くノ一ぼたん 其の12.mp4", 12),
        ],
    )
    def test_parse_sono_format(self, parser_service, filename, expected_episode):
        """Test 其の format parsing."""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode
        assert result.is_parsed is True

    # ===== 其ノ/其之/其乃 变体测试 =====
    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            ("剧名 其ノ一.mp4", 1),
            ("剧名 其ノ二.mp4", 2),
            ("剧名 其ノ3.mp4", 3),
            ("剧名 其之一.mp4", 1),
            ("剧名 其之弐.mp4", 2),
            ("剧名 其乃参.mp4", 3),
        ],
    )
    def test_parse_sono_variants(self, parser_service, filename, expected_episode):
        """Test 其の variant forms parsing."""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode

    # ===== 第X話 格式测试 =====
    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            ("进击的巨人 第1話.mp4", 1),
            ("进击的巨人 第12話.mp4", 12),
            ("进击的巨人 第一話.mp4", 1),
            ("进击的巨人 第十二話.mp4", 12),
            ("进击的巨人 第1集.mp4", 1),
            ("进击的巨人 第1回.mp4", 1),
            ("进击的巨人 第1章.mp4", 1),
        ],
    )
    def test_parse_dai_format(self, parser_service, filename, expected_episode):
        """Test 第X話 format parsing."""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode
        assert result.is_parsed is True
        # 无显式季号时应默认 season=1（避免记录页季/集列空白）
        assert result.season == 1

    # ===== 前編/後編 测试 =====
    @pytest.mark.parametrize(
        "filename,expected_season,expected_episode",
        [
            ("OVA 前編.mp4", 1, 1),
            ("OVA 後編.mp4", 1, 2),
            ("剧名 上巻.mp4", 1, 1),
            ("剧名 下巻.mp4", 1, 2),
            ("剧名 中編.mp4", 1, 2),
        ],
    )
    def test_parse_zengo_format(self, parser_service, filename, expected_season, expected_episode):
        """Test 前編/後編 format parsing."""
        result = parser_service.parse(filename)
        assert result.season == expected_season
        assert result.episode == expected_episode

    # ===== 特别篇/发行形态标记测试 =====
    # OVA/OAD/ONA 是发行形态而非季号（实测 [260828][ばにぃうぉ～か～]OVA 彼女催眠 ＃1）：
    # 早先据标记落成 season=0，而第 0 季在下游被全面过滤（季集选择器只列 >0，
    # TMDB 也拿不到对应集），文件会卡在 S00E01 且无法手动改，故标记不再决定季号。
    @pytest.mark.parametrize(
        "filename",
        [
            "剧名 特別編.mp4",
            "剧名 番外篇.mp4",
            "剧名 OVA.mp4",
            "剧名 OAD.mp4",
        ],
    )
    def test_parse_special_marker_keeps_season_1(self, parser_service, filename):
        """特别篇/发行形态标记不再落成第 0 季。"""
        result = parser_service.parse(filename)
        assert result.season == 1
        assert result.episode == 1

    def test_user_case_ova_hash_episode(self, parser_service):
        """实测场景：[260828][ばにぃうぉ～か～]OVA 彼女催眠 ＃1 应解析为 S01E01。"""
        result = parser_service.parse("[260828][ばにぃうぉ～か～]OVA 彼女催眠 ＃1.cht.mp4")

        assert result.season == 1
        assert result.episode == 1
        assert result.series_name == "彼女催眠"

    def test_user_case_ova_hash_episode_2(self, parser_service):
        """同系列 ＃2 应解析为 S01E02（全角井号集数）。"""
        result = parser_service.parse("[260828][ばにぃうぉ～か～]OVA 彼女催眠 ＃2.cht.mp4")

        assert result.season == 1
        assert result.episode == 2

    # ===== 用户问题场景测试 =====
    def test_user_case_sono_ni(self, parser_service):
        """Test user's actual case: 其の弍 should be episode 2."""
        filename = "[251128][Queen Bee]好色の忠義くノ一ぼたん 其の弍[田辺京].cht.mp4"
        result = parser_service.parse(filename)

        assert result.episode == 2, f"Expected episode 2, got {result.episode}"
        assert "好色の忠義くノ一ぼたん" in (result.series_name or "")
        assert result.is_parsed is True

    def test_user_case_sono_san(self, parser_service):
        """Test: 其の参 should be episode 3."""
        filename = "[251128][Queen Bee]好色の忠義くノ一ぼたん 其の参[田辺京].cht.mp4"
        result = parser_service.parse(filename)

        assert result.episode == 3, f"Expected episode 3, got {result.episode}"

    # ===== 罗马数字测试 =====
    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            ("剧名 Ⅰ.mp4", 1),
            ("剧名 Ⅱ.mp4", 2),
            ("剧名 Ⅲ.mp4", 3),
            ("剧名 Ⅳ.mp4", 4),
            ("剧名 Ⅴ.mp4", 5),
        ],
    )
    def test_parse_roman_numerals(self, parser_service, filename, expected_episode):
        """罗马数字集数（Ⅰ-Ⅻ）应解析成对应集号。"""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode

    # ===== 全角数字测试 =====
    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            ("剧名 第１話.mp4", 1),
            ("剧名 第１２話.mp4", 12),
            ("剧名 ＃１.mp4", 1),
            ("剧名 ＃１２.mp4", 12),
        ],
    )
    def test_parse_fullwidth_numbers(self, parser_service, filename, expected_episode):
        """Test fullwidth number parsing."""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode

    # ===== 智能清洗测试 =====
    def test_smart_cleaning_preserves_episode_bracket(self, parser_service):
        """Test that episode number in brackets is preserved."""
        filename = "[字幕组] 剧名 [01].mp4"
        result = parser_service.parse(filename)
        assert result.episode == 1

    def test_smart_cleaning_removes_noise(self, parser_service):
        """Test that noise brackets are removed."""
        filename = "[251128][Queen Bee]剧名[作者名].mp4"
        result = parser_service.parse(filename)
        # 日期前缀和制作组应该被移除
        assert "251128" not in (result.series_name or "")
        assert "Queen Bee" not in (result.series_name or "")


class TestRealWorldFilenames:
    """回归测试：真实发布文件名与常见命名习惯。

    用例来源：data/scraper.db 中已刮削记录的 folder_path，以及实测遇到的命名形式。
    修复前这些用例分别出现「剧名带日期/制作组」「剧名整条丢失」「集数抽不到」。
    """

    @pytest.fixture
    def parser_service(self):
        """Provide a ParserService instance for testing."""
        return ParserService()

    # ===== 发布日期前缀 =====
    @pytest.mark.parametrize(
        "filename,expected_name,expected_episode",
        [
            # 缺失左侧方括号：实测遇到的真实文件名
            ("260703][GOLD BEAR]蹂躙王国 前編 ～エルフ王国は巨大苗床に～.chs.mp4", "蹂躙王国", 1),
            # 下划线分隔的日期前缀
            ("260703_作品名_第01話.mp4", "作品名", 1),
            # YYYYMMDD 与带短横线的日期
            ("[20191114]作品名 第3話.mp4", "作品名", 3),
            ("[2019-05-06]作品名 第3話.mp4", "作品名", 3),
            # 6 位数字但不是合法日期（月 23）不应被当成日期
            ("[012345]作品名 第3話.mp4", "作品名", 3),
        ],
    )
    def test_date_prefix(self, parser_service, filename, expected_name, expected_episode):
        """发布日期前缀应被移除，且不误伤数字开头的剧名。"""
        result = parser_service.parse(filename)
        assert result.series_name == expected_name
        assert result.episode == expected_episode

    # ===== 方括号结构 =====
    @pytest.mark.parametrize(
        "filename,expected_name",
        [
            # 集数被方括号单独包住，剧名也在方括号里
            ("[1080p][作品名][第1話].mkv", "作品名"),
            ("[字幕组][作品名][01].mp4", "作品名"),
            ("[字幕组]作品名[第2話][1080p].mp4", "作品名"),
        ],
    )
    def test_bracket_structure(self, parser_service, filename, expected_name):
        """方括号结构下剧名不应丢失或残留括号。"""
        result = parser_service.parse(filename)
        assert result.series_name == expected_name
        assert "[" not in (result.series_name or "")
        assert result.episode is not None

    # ===== 集数抽取 =====
    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            # 裸数字后跟副标题 / 年份 / 分辨率方括号
            ("[260703][ピンクパイナップル]作品名 1 ～サブ～.mp4", 1),
            ("作品名 01 (2023) [1080p].mp4", 1),
            ("作品名 1 ～サブ～.mp4", 1),
            # 平假名 その
            ("作品名 その1.mp4", 1),
            ("作品名 その参.mp4", 3),
        ],
    )
    def test_bare_number_episode(self, parser_service, filename, expected_episode):
        """裸数字与平假名 その 也应解析出集数。"""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode

    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            # 集数范围（多集文件）取首集
            ("作品名 1-2.mp4", 1),
            ("作品名 第1-2話.mp4", 1),
            ("作品名 01-02.mkv", 1),
        ],
    )
    def test_episode_range_takes_first(self, parser_service, filename, expected_episode):
        """范围集数取首集，剧名不应残留范围文本。"""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode
        assert result.series_name == "作品名"

    @pytest.mark.parametrize(
        "filename,expected_episode",
        [
            # 4 位数字（分辨率/年份）不应被当成集数
            ("作品名 1920x1080.mp4", None),
            ("作品名 2023.mp4", None),
        ],
    )
    def test_resolution_and_year_not_episode(self, parser_service, filename, expected_episode):
        """分辨率与年份不能被误判成集数。"""
        result = parser_service.parse(filename)
        assert result.episode == expected_episode

    # ===== 剧名边界补强 =====
    @pytest.mark.parametrize(
        "filename,expected_name",
        [
            ("作品名 完結編.mp4", "作品名"),
            ("剧名 最終編.mp4", "剧名"),
        ],
    )
    def test_final_chapter_boundary(self, parser_service, filename, expected_name):
        """完結編/最終編 也是剧名边界。"""
        result = parser_service.parse(filename)
        assert result.series_name == expected_name

    # ===== 方括号承载剧名 =====
    @pytest.mark.parametrize(
        "filename,expected_name",
        [
            # 括号内只有剧名 + 集数标记，不是制作组，不能删
            ("[剧名] 第1話.mp4", "剧名"),
            ("[作品名 第1話] サブ.mp4", "作品名"),
            ("[字幕组][作品名][01].mp4", "作品名"),
        ],
    )
    def test_bracket_carries_series_name(self, parser_service, filename, expected_name):
        """方括号承载剧名时不能被当成制作组删掉。"""
        result = parser_service.parse(filename)
        assert result.series_name == expected_name

    def test_episode_only_bracket_has_no_series_name(self, parser_service):
        """只有集数标记的文件名不应产出纯数字剧名。"""
        result = parser_service.parse("[01].mp4")
        assert result.series_name is None
        assert result.episode == 1


class TestCleanedNameParsing:
    """集数解析统一走清洗后的名字。

    清洗器剥掉的日期前缀、制作组方括号、画质标记、副标题，过去仍参与集数匹配，
    导致 [720] 被当成第 720 集。这里逐条钉住这些真实文件名。
    """

    @pytest.mark.parametrize(
        "filename,expected_name,expected_episode",
        [
            ("[251114] 作品名 [720] 第1話.mp4", "作品名", 1),
            ("[720] 作品名 第1話.mp4", "作品名", 1),
            ("260703]作品名 01.mp4", "作品名", 1),
            ("[Queen Bee] 作品名 第2話 [1080p].mp4", "作品名", 2),
            ("[作品名][第1話].mp4", "作品名", 1),
            ("作品名 Ⅱ.mp4", "作品名", 2),
        ],
    )
    def test_cleaned_name_drives_episode_parsing(
        self, parser_service, filename, expected_name, expected_episode
    ):
        result = parser_service.parse(filename)
        assert result.series_name == expected_name
        assert result.episode == expected_episode

    def test_title_with_sp_is_not_truncated(self, parser_service):
        """SP 若无字母边界，SPY×FAMILY 会被当成特别篇标记把标题截空。"""
        result = parser_service.parse("SPY×FAMILY 第1話.mp4")
        assert result.series_name == "SPY×FAMILY"
        assert result.episode == 1

    def test_ova_without_number_falls_back_to_episode_1(self, parser_service):
        result = parser_service.parse("作品名 OVA.mp4")
        assert result.episode == 1
        assert result.season == 1


class TestStudioPrefixFallback:
    """制作组粘在剧名开头时的搜索降级（不是解析改写）。"""

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("魔人○○交配", "○○交配"),
            ("nurハメハラ", "ハメハラ"),
            ("ピンクパイナップル無自覚な幼馴染", "無自覚な幼馴染"),
        ],
    )
    def test_strips_glued_studio_name(self, title, expected):
        assert strip_known_group_prefix(title) == expected

    @pytest.mark.parametrize("title", ["魔人", "無自覚な幼馴染", ""])
    def test_returns_none_when_not_applicable(self, title):
        assert strip_known_group_prefix(title) is None

    def test_volume_marker_counts_as_episode(self, parser_service):
        """第1巻 是卷号，对这类作品等同于第 1 集；顺带把残留的 chs 语言标记去掉。"""
        result = parser_service.parse("作品名 THE ANIMATION 第1巻 chs.mp4")

        assert result.episode == 1
        assert "chs" not in (result.series_name or "")


class TestStudioNameTable:
    """制作组清单本身的质量：每个条目都能命中自己，且不吃掉正常标题。"""

    @pytest.mark.parametrize("name", _STUDIO_NAMES)
    def test_every_entry_strips_itself(self, name):
        # 用「名字 + 作品名」验证：既能被识别，又不会把后面的作品名一起吃掉
        assert strip_known_group_prefix(f"{name}作品名") == "作品名"

    @pytest.mark.parametrize(
        "title,expected",
        [
            # 下划线与空格混用（真实文件名：260911Queen_Bee村又さんの愛情_…）
            ("Queen_Bee村又さんの愛情", "村又さんの愛情"),
            ("Queen Bee村又さんの愛情", "村又さんの愛情"),
            # 中黑版本
            ("ピンク・パイナップル無自覚な幼馴染", "無自覚な幼馴染"),
            ("メリー・ジェーン小女ラムネ", "小女ラムネ"),
            # 日语名后直接跟拉丁标题，不能被字母边界挡住
            ("魔人NTR騎士", "NTR騎士"),
            # 新增的制作组
            ("ショーテン性指導員のお仕事", "性指導員のお仕事"),
            ("ヌールハメハラ", "ハメハラ"),
        ],
    )
    def test_common_spellings(self, title, expected):
        assert strip_known_group_prefix(title) == expected

    @pytest.mark.parametrize(
        "title",
        [
            "nurse angel",
            "naruto",
            "Arms 第1話",  # 剥完只剩集数标记，不算有效剧名
            "無自覚な幼馴染",
        ],
    )
    def test_latin_titles_are_not_mangled(self, title):
        assert strip_known_group_prefix(title) is None
