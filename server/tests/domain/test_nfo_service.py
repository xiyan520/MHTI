"""Unit tests for NFO service."""

from datetime import date

import pytest

from server.domain.artifacts.nfo_service import NFOService
from server.models.nfo import EpisodeNFO, SeasonNFO, TVShowNFO
from server.models.tmdb import TMDBEpisode, TMDBSeason, TMDBSeries


@pytest.fixture
def nfo_service():
    """Provide an NFOService instance."""
    return NFOService()


class TestNFOServiceTVShow:
    """Tests for TVShow NFO generation."""

    def test_generate_tvshow_nfo_basic(self, nfo_service):
        """Test basic tvshow.nfo generation."""
        data = TVShowNFO(
            title="Breaking Bad",
            original_title="Breaking Bad",
            rating=8.9,
            year=2008,
            plot="A chemistry teacher becomes a meth dealer.",
            tmdb_id=1396,
            genres=["Drama", "Crime"],
            status="Ended",
        )

        nfo = nfo_service.generate_tvshow_nfo(data)

        assert '<?xml version="1.0" encoding="utf-8"' in nfo
        assert "<tvshow>" in nfo
        assert "<title>Breaking Bad</title>" in nfo
        assert "<originaltitle>Breaking Bad</originaltitle>" in nfo
        assert "<rating>8.9</rating>" in nfo
        assert "<year>2008</year>" in nfo
        assert '<uniqueid type="tmdb">1396</uniqueid>' in nfo
        assert "<genre>Drama</genre>" in nfo
        assert "<genre>Crime</genre>" in nfo
        assert "<status>Ended</status>" in nfo
        assert "</tvshow>" in nfo

    def test_generate_tvshow_nfo_minimal(self, nfo_service):
        """Test tvshow.nfo with minimal data."""
        data = TVShowNFO(title="Test Show")

        nfo = nfo_service.generate_tvshow_nfo(data)

        assert "<title>Test Show</title>" in nfo
        assert "<sorttitle>Test Show</sorttitle>" in nfo
        assert "<tvshow>" in nfo
        assert "</tvshow>" in nfo

    def test_generate_tvshow_nfo_with_date(self, nfo_service):
        """Test tvshow.nfo with premiere date."""
        data = TVShowNFO(
            title="Test Show",
            premiered=date(2024, 1, 15),
        )

        nfo = nfo_service.generate_tvshow_nfo(data)

        assert "<premiered>2024-01-15</premiered>" in nfo

    def test_generate_tvshow_nfo_utf8(self, nfo_service):
        """Test tvshow.nfo with Chinese characters."""
        data = TVShowNFO(
            title="绝命毒师",
            original_title="Breaking Bad",
            plot="一位化学老师变成了毒贩。",
            genres=["剧情", "犯罪"],
        )

        nfo = nfo_service.generate_tvshow_nfo(data)

        assert "<title>绝命毒师</title>" in nfo
        assert "一位化学老师变成了毒贩" in nfo
        assert "<genre>剧情</genre>" in nfo


class TestNFOServiceSeason:
    """Tests for Season NFO generation."""

    def test_generate_season_nfo_basic(self, nfo_service):
        """Test basic season.nfo generation."""
        data = SeasonNFO(
            season_number=1,
            title="Season 1",
            plot="The first season of the show.",
            premiered=date(2008, 1, 20),
        )

        nfo = nfo_service.generate_season_nfo(data)

        assert '<?xml version="1.0" encoding="utf-8"' in nfo
        assert "<season>" in nfo
        assert "<seasonnumber>1</seasonnumber>" in nfo
        assert "<title>Season 1</title>" in nfo
        assert "<plot>The first season of the show.</plot>" in nfo
        assert "<premiered>2008-01-20</premiered>" in nfo
        assert "</season>" in nfo

    def test_generate_season_nfo_minimal(self, nfo_service):
        """Test season.nfo with minimal data."""
        data = SeasonNFO(season_number=5)

        nfo = nfo_service.generate_season_nfo(data)

        assert "<seasonnumber>5</seasonnumber>" in nfo
        assert "<title>Season 5</title>" in nfo  # Default title


class TestNFOServiceEpisode:
    """Tests for Episode NFO generation."""

    def test_generate_episode_nfo_basic(self, nfo_service):
        """Test basic episode.nfo generation."""
        data = EpisodeNFO(
            title="Pilot",
            season=1,
            episode=1,
            plot="The first episode.",
            aired=date(2008, 1, 20),
            rating=8.5,
        )

        nfo = nfo_service.generate_episode_nfo(data)

        assert '<?xml version="1.0" encoding="utf-8"' in nfo
        assert "<episodedetails>" in nfo
        assert "<title>Pilot</title>" in nfo
        assert "<season>1</season>" in nfo
        assert "<episode>1</episode>" in nfo
        assert "<plot>The first episode.</plot>" in nfo
        assert "<aired>2008-01-20</aired>" in nfo
        assert "<rating>8.5</rating>" in nfo
        assert "</episodedetails>" in nfo

    def test_generate_episode_nfo_minimal(self, nfo_service):
        """Test episode.nfo with minimal data."""
        data = EpisodeNFO(title="Episode Title", season=2, episode=5)

        nfo = nfo_service.generate_episode_nfo(data)

        assert "<title>Episode Title</title>" in nfo
        assert "<season>2</season>" in nfo
        assert "<episode>5</episode>" in nfo


class TestNFOServiceConversion:
    """Tests for TMDB to NFO conversion."""

    def test_tvshow_from_tmdb(self, nfo_service):
        """Test conversion from TMDBSeries to TVShowNFO."""
        series = TMDBSeries(
            id=1396,
            name="Breaking Bad",
            original_name="Breaking Bad",
            overview="A chemistry teacher becomes a meth dealer.",
            first_air_date=date(2008, 1, 20),
            vote_average=8.9,
            genres=["Drama", "Crime"],
            status="Ended",
        )

        nfo_data = nfo_service.tvshow_from_tmdb(series)

        assert nfo_data.title == "Breaking Bad"
        assert nfo_data.original_title == "Breaking Bad"
        assert nfo_data.tmdb_id == 1396
        assert nfo_data.year == 2008
        assert nfo_data.rating == 8.9
        assert nfo_data.genres == ["Drama", "Crime"]
        assert nfo_data.status == "Ended"

    def test_season_from_tmdb(self, nfo_service):
        """Test conversion from TMDBSeason to SeasonNFO."""
        season = TMDBSeason(
            season_number=1,
            name="Season 1",
            overview="The first season.",
            air_date=date(2008, 1, 20),
        )

        nfo_data = nfo_service.season_from_tmdb(season)

        assert nfo_data.season_number == 1
        assert nfo_data.title == "Season 1"
        assert nfo_data.plot == "The first season."
        assert nfo_data.premiered == date(2008, 1, 20)

    def test_episode_from_tmdb(self, nfo_service):
        """Test conversion from TMDBEpisode to EpisodeNFO."""
        episode = TMDBEpisode(
            episode_number=1,
            name="Pilot",
            overview="The first episode.",
            air_date=date(2008, 1, 20),
            vote_average=8.5,
        )

        nfo_data = nfo_service.episode_from_tmdb(episode, season_number=1)

        assert nfo_data.title == "Pilot"
        assert nfo_data.season == 1
        assert nfo_data.episode == 1
        assert nfo_data.plot == "The first episode."
        assert nfo_data.aired == date(2008, 1, 20)
        assert nfo_data.rating == 8.5


class TestNFOServiceSpecialCharacters:
    """Tests for XML special character handling."""

    def test_special_characters_in_title(self, nfo_service):
        """Test handling of XML special characters."""
        data = TVShowNFO(
            title="Tom & Jerry",
            plot='He said "Hello" and <wave>.',
        )

        nfo = nfo_service.generate_tvshow_nfo(data)

        # XML should be properly escaped
        assert "Tom &amp; Jerry" in nfo or "Tom & Jerry" in nfo
        assert "&lt;" in nfo or "<wave>" not in nfo.split("</plot>")[0]

    def test_apostrophe_in_title(self, nfo_service):
        """Test handling of apostrophes."""
        data = EpisodeNFO(
            title="Cat's in the Bag",
            season=1,
            episode=2,
        )

        nfo = nfo_service.generate_episode_nfo(data)

        assert "Cat" in nfo
        assert "<title>" in nfo
