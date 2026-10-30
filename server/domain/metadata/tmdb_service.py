"""TMDB service for API-based metadata retrieval."""

from datetime import date, datetime

import httpx

from server.common.exceptions import (
    TMDBConnectionError,
    TMDBNotConfiguredError,
    TMDBNotFoundError,
    TMDBTimeoutError,
)
from server.domain.system.config_service import ConfigService
from server.models.config import ApiTokenStatus
from server.models.tmdb import (
    TMDBEpisode,
    TMDBSearchResponse,
    TMDBSearchResult,
    TMDBSeason,
    TMDBSeries,
)

TMDB_BASE_URL = "https://www.themoviedb.org"
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"

# R18（成人内容）探测关键词：TMDB 不支持查询「账户是否开启成人内容」，
# 只能用带 include_adult=true 的搜索看能否真的拿到 adult=true 的结果。
# 实测（2026-09-26，同一关键词对照）：
#   hentai  include_adult=true → 10 条含 5 条 adult；false → 5 条、0 条 adult
#   無修正  include_adult=true → 5 条含 2 条 adult；false → 3 条、0 条 adult
# 两个词互为备份：第一个词若因 TMDB 词条变动而失效，第二个仍能给出结论。
ADULT_PROBE_KEYWORDS = ("hentai", "無修正")


class TMDBService:
    """Service for TMDB operations using API."""

    def __init__(self, config_service: ConfigService):
        """Initialize TMDB service with explicit dependency."""
        self.config_service = config_service

    async def _get_proxy_url(self) -> str | None:
        """Get proxy URL from config."""
        config = await self.config_service.get_proxy_config()
        return config.get_url()

    async def _get_language(self) -> str:
        """Get primary language from config."""
        config = await self.config_service.get_language_config()
        return config.primary

    async def _get_api_token(self) -> str | None:
        """Get stored API token."""
        return await self.config_service.get_api_token()

    async def _get_timeout(self) -> float:
        """Get timeout from SystemConfig."""
        config = await self.config_service.get_system_config()
        return float(config.task_timeout)

    def _is_bearer_token(self, token: str) -> bool:
        """Check if token is a Bearer token (JWT format) or API Key."""
        return token.startswith("eyJ")

    def _format_proxy_error(self, error: Exception) -> str:
        """Format proxy-related runtime errors for user-facing responses."""
        message = str(error)
        if isinstance(error, ImportError) and "socksio" in message:
            return "SOCKS5 代理缺少运行依赖，请安装 httpx[socks]"
        return message

    async def _make_api_request(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> httpx.Response:
        """
        Make HTTP request to TMDB API with authentication.

        Raises:
            TMDBNotConfiguredError: API Token 未配置
            TMDBTimeoutError: 请求超时
            TMDBConnectionError: 连接失败
        """
        token = await self._get_api_token()
        if not token:
            raise TMDBNotConfiguredError("API Token")

        proxy_url = await self._get_proxy_url()
        timeout = await self._get_timeout()
        url = f"{TMDB_API_BASE_URL}{endpoint}"

        try:
            if self._is_bearer_token(token):
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
                async with httpx.AsyncClient(
                    timeout=timeout,
                    proxy=proxy_url,
                ) as client:
                    return await client.get(url, headers=headers, params=params)
            else:
                headers = {"Accept": "application/json"}
                api_params = {"api_key": token}
                if params:
                    api_params.update(params)
                async with httpx.AsyncClient(
                    timeout=timeout,
                    proxy=proxy_url,
                ) as client:
                    return await client.get(url, headers=headers, params=api_params)
        except httpx.TimeoutException:
            raise TMDBTimeoutError(endpoint)
        except httpx.RequestError as e:
            raise TMDBConnectionError(str(e))

    async def test_proxy(self, proxy_url: str | None = None) -> tuple[bool, str, int | None]:
        """
        Test proxy connection to TMDB.

        Args:
            proxy_url: Optional proxy URL to test. If None, uses configured proxy.

        Returns:
            Tuple of (success, message, latency_ms).
        """
        import time

        if proxy_url is None:
            proxy_url = await self._get_proxy_url()

        try:
            start = time.time()
            timeout = await self._get_timeout()
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
                response = await client.get(
                    TMDB_BASE_URL,
                    headers={"User-Agent": "Mozilla/5.0"},
                    follow_redirects=True,
                )
            latency = int((time.time() - start) * 1000)

            if response.status_code == 200:
                return True, "连接成功", latency
            else:
                return False, f"HTTP 错误: {response.status_code}", latency

        except httpx.TimeoutException:
            return False, "连接超时", None
        except httpx.ProxyError as e:
            return False, f"代理错误: {str(e)}", None
        except httpx.RequestError as e:
            return False, f"连接错误: {str(e)}", None
        except ImportError as e:
            return False, f"测试失败: {self._format_proxy_error(e)}", None
        except Exception as e:
            return False, f"测试失败: {str(e)}", None

    # ========== Utility Methods ==========

    def get_image_url(self, path: str | None, size: str = "w500") -> str | None:
        """
        Get full image URL from TMDB path.

        Args:
            path: Image path from TMDB (e.g., "/abc123.jpg")
            size: Image size (w92, w154, w185, w342, w500, w780, original)

        Returns:
            Full image URL or None if path is empty.
        """
        if not path:
            return None
        return f"{TMDB_IMAGE_BASE_URL}/{size}{path}"

    def _parse_date(self, date_str: str | None) -> date | None:
        """Parse date string to date object."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None

    # ========== API Token Methods ==========

    async def verify_api_token(self, token: str) -> tuple[bool, str | None]:
        """
        Verify API token by making a test request.

        Supports both API Key (v3) and Bearer Token (v4).

        Args:
            token: The API token to verify.

        Returns:
            Tuple of (is_valid, error_message).
        """
        try:
            proxy_url = await self._get_proxy_url()
            url = f"{TMDB_API_BASE_URL}/configuration"

            if self._is_bearer_token(token):
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
                params = None
            else:
                headers = {"Accept": "application/json"}
                params = {"api_key": token}

            timeout = await self._get_timeout()
            async with httpx.AsyncClient(timeout=timeout, proxy=proxy_url) as client:
                response = await client.get(url, headers=headers, params=params)

                if response.status_code == 200:
                    return True, None
                elif response.status_code == 401:
                    try:
                        error_data = response.json()
                        status_message = error_data.get("status_message", "")
                        if status_message:
                            return False, f"API Token 验证失败: {status_message}"
                    except Exception:
                        pass
                    return False, "API Token 无效或已过期"
                else:
                    try:
                        error_data = response.json()
                        status_message = error_data.get("status_message", "")
                        if status_message:
                            return False, f"验证失败: {status_message}"
                    except Exception:
                        pass
                    return False, f"验证失败: HTTP {response.status_code}"

        except httpx.TimeoutException:
            return False, "连接超时 - 请检查网络或代理设置"
        except httpx.RequestError as e:
            return False, f"连接错误: {str(e)}"
        except ImportError as e:
            return False, self._format_proxy_error(e)
        except Exception as e:
            return False, f"验证失败: {str(e)}"

    async def save_and_verify_api_token(self, token: str) -> ApiTokenStatus:
        """
        Verify API token first, then save if valid.

        Args:
            token: The API token to save.

        Returns:
            ApiTokenStatus with verification results.
        """
        if not token or not token.strip():
            return ApiTokenStatus(
                is_configured=False,
                is_valid=False,
                error_message="API Token 不能为空",
            )

        # 先验证 token
        is_valid, error = await self.verify_api_token(token.strip())

        if not is_valid:
            # 验证失败，不保存
            return ApiTokenStatus(
                is_configured=False,
                is_valid=False,
                error_message=error,
            )

        # 验证成功，保存 token
        await self.config_service.save_api_token(token.strip())
        await self.config_service.set_api_token_verified(True)

        # 保存即检测 R18：这是里番工具的核心能力，缺它时刮削只会报「未找到匹配」
        enabled, adult_message = await self.check_adult_access()
        await self.config_service.save_adult_status(enabled, adult_message)

        is_valid, verified_at = await self.config_service.get_api_token_verification()

        return await self.get_api_token_status()

    async def get_api_token_status(self) -> ApiTokenStatus:
        """Get current API token configuration status."""
        return await self.config_service.get_api_token_status()

    async def delete_api_token(self) -> bool:
        """Delete the stored API token."""
        return await self.config_service.delete_api_token()

    # ========== R18（成人内容）检测 ==========

    async def check_adult_access(self) -> tuple[bool | None, str]:
        """
        探测当前凭据能否拿到 R18（成人）内容。

        TMDB 没有提供「账户是否开启成人内容」的查询接口，只能实测：带
        ``include_adult=true`` 搜索成人向关键词，看返回里有没有 ``adult=true`` 的条目。
        本仓库刮削只保留 ``adult=true`` 的结果（scraping/service.py），所以账户一旦在
        官网隐藏成人内容，刮削会安静地报「未找到匹配的成人剧集」——本方法用来提前把它变成
        一个可读的结论。

        Returns:
            ``(enabled, message)``：
            - ``True``  探测到成人内容
            - ``False`` 探测正常但全无成人条目（账户关了成人内容）
            - ``None``  无法判定（未配置 Token / 凭据失效 / 网络代理 / 关键词都没结果）
        """
        token = await self._get_api_token()
        if not token:
            return None, "未配置 API Token，无法检测 R18"

        probed_keywords = 0
        try:
            for keyword in ADULT_PROBE_KEYWORDS:
                response = await self._make_api_request(
                    "/search/tv",
                    params={
                        "query": keyword,
                        "language": "zh-CN",
                        "include_adult": "true",
                    },
                )

                if response.status_code in (401, 403):
                    return None, "API Token 无效或已过期，无法判断 R18 状态"
                if response.status_code != 200:
                    return None, f"TMDB 返回 HTTP {response.status_code}，无法判断 R18 状态"

                results = response.json().get("results", [])
                if any(item.get("adult") for item in results):
                    return True, f"已开启（关键词「{keyword}」命中成人内容）"
                if results:
                    probed_keywords += 1

            if probed_keywords == 0:
                # 关键词都搜不到东西：可能是 TMDB 词条变动，不能断言 R18 被关
                return None, "无法判断：探测关键词均无结果，请手动搜索成人剧集确认"

            return (
                False,
                "未开启：TMDB 账户设置里隐藏了成人内容，刮削会找不到任何结果",
            )
        except TMDBTimeoutError:
            return None, "检测超时，请检查网络或代理设置"
        except TMDBConnectionError as e:
            return None, f"检测失败：{e}"

    async def refresh_token_status(self) -> ApiTokenStatus:
        """
        远端重新验证 Token 并重新探测 R18，结果落库后返回最新状态。

        设置页的「重新检测」走这里：之前它只是重新读一遍数据库里的旧状态（不会真的打
        TMDB），所以 Token 在 TMDB 那边被删掉后仍然显示「已配置且有效」。
        """
        token = await self._get_api_token()
        if not token:
            return ApiTokenStatus(is_configured=False)

        is_valid, error = await self.verify_api_token(token)
        await self.config_service.set_api_token_verified(bool(is_valid))

        if not is_valid:
            # Token 失效时不动旧的 R18 结论：失效不是 R18 状态变化
            return await self.get_api_token_status()

        enabled, message = await self.check_adult_access()
        await self.config_service.save_adult_status(enabled, message)
        return await self.get_api_token_status()

    # ========== API-based Methods ==========

    async def search_series_by_api(
        self,
        query: str,
        language: str | None = None,
    ) -> TMDBSearchResponse:
        """
        Search TV series using TMDB API.

        Args:
            query: Search query string
            language: Language for results

        Returns:
            TMDBSearchResponse with search results.
        """
        if language is None:
            language = await self._get_language()

        try:
            response = await self._make_api_request(
                "/search/tv",
                params={"query": query, "language": language, "include_adult": "true"},
            )

            if response.status_code != 200:
                return TMDBSearchResponse(query=query, total_results=0, results=[])

            data = response.json()
            results = []

            for item in data.get("results", [])[:20]:
                first_air_date = None
                if item.get("first_air_date"):
                    try:
                        first_air_date = date.fromisoformat(item["first_air_date"])
                    except ValueError:
                        pass

                results.append(
                    TMDBSearchResult(
                        id=item["id"],
                        name=item.get("name", ""),
                        original_name=item.get("original_name"),
                        first_air_date=first_air_date,
                        poster_path=item.get("poster_path"),
                        overview=item.get("overview"),
                        vote_average=item.get("vote_average"),
                        adult=item.get("adult", False),
                    )
                )

            return TMDBSearchResponse(
                query=query,
                total_results=data.get("total_results", len(results)),
                results=results,
            )

        except ValueError:
            raise
        except (httpx.TimeoutException, httpx.RequestError):
            raise

    async def get_series_by_api(
        self,
        tmdb_id: int,
        language: str | None = None,
    ) -> TMDBSeries | None:
        """
        Get TV series details from TMDB API.

        Args:
            tmdb_id: TMDB series ID
            language: Language for metadata (uses config if not specified)

        Returns:
            TMDBSeries with full details, or None if not found.
        """
        if language is None:
            language = await self._get_language()

        try:
            response = await self._make_api_request(
                f"/tv/{tmdb_id}",
                params={"language": language},
            )

            if response.status_code == 404:
                return None
            if response.status_code != 200:
                return None

            data = response.json()
            return self._parse_series_json(data)

        except ValueError:
            raise
        except (httpx.TimeoutException, httpx.RequestError):
            raise

    def _parse_series_json(self, data: dict) -> TMDBSeries:
        """Parse series data from API JSON response."""
        genres = [g["name"] for g in data.get("genres", [])]

        seasons = []
        for s in data.get("seasons", []):
            seasons.append(
                TMDBSeason(
                    season_number=s.get("season_number", 0),
                    name=s.get("name", ""),
                    overview=s.get("overview"),
                    air_date=self._parse_date(s.get("air_date")),
                    poster_path=s.get("poster_path"),
                    episode_count=s.get("episode_count"),
                )
            )

        return TMDBSeries(
            id=data["id"],
            name=data.get("name", ""),
            original_name=data.get("original_name"),
            overview=data.get("overview"),
            first_air_date=self._parse_date(data.get("first_air_date")),
            vote_average=data.get("vote_average"),
            poster_path=data.get("poster_path"),
            backdrop_path=data.get("backdrop_path"),
            genres=genres,
            status=data.get("status"),
            number_of_seasons=data.get("number_of_seasons"),
            number_of_episodes=data.get("number_of_episodes"),
            seasons=seasons,
        )

    async def get_season_by_api(
        self,
        tmdb_id: int,
        season_number: int,
        language: str | None = None,
    ) -> TMDBSeason | None:
        """
        Get season details including episodes from TMDB API.

        Args:
            tmdb_id: TMDB series ID
            season_number: Season number
            language: Language for metadata (uses config if not specified)

        Returns:
            TMDBSeason with episodes, or None if not found.
        """
        if language is None:
            language = await self._get_language()

        try:
            response = await self._make_api_request(
                f"/tv/{tmdb_id}/season/{season_number}",
                params={"language": language},
            )

            if response.status_code == 404:
                return None
            if response.status_code != 200:
                return None

            data = response.json()
            return self._parse_season_json(data)

        except ValueError:
            raise
        except (httpx.TimeoutException, httpx.RequestError):
            raise

    def _parse_season_json(self, data: dict) -> TMDBSeason:
        """Parse season data from API JSON response."""
        episodes = []
        for ep in data.get("episodes", []):
            episodes.append(
                TMDBEpisode(
                    episode_number=ep.get("episode_number", 0),
                    name=ep.get("name", ""),
                    overview=ep.get("overview"),
                    air_date=self._parse_date(ep.get("air_date")),
                    vote_average=ep.get("vote_average"),
                    still_path=ep.get("still_path"),
                )
            )

        return TMDBSeason(
            season_number=data.get("season_number", 0),
            name=data.get("name", ""),
            overview=data.get("overview"),
            air_date=self._parse_date(data.get("air_date")),
            poster_path=data.get("poster_path"),
            episode_count=len(episodes),
            episodes=episodes,
        )

    async def get_series_with_episodes(
        self,
        tmdb_id: int,
        language: str | None = None,
        include_episodes: bool = True,
    ) -> TMDBSeries | None:
        """
        Get TV series details with full episode information.

        Args:
            tmdb_id: TMDB series ID
            language: Language for metadata
            include_episodes: Whether to fetch episode details for each season

        Returns:
            TMDBSeries with complete season/episode data, or None if not found.
        """
        series = await self.get_series_by_api(tmdb_id, language)

        if series is None:
            return None

        if not include_episodes or not series.seasons:
            return series

        updated_seasons = []
        for season in series.seasons:
            if season.season_number == 0:
                updated_seasons.append(season)
                continue

            try:
                season_detail = await self.get_season_by_api(
                    tmdb_id, season.season_number, language
                )

                if season_detail and season_detail.episodes:
                    updated_seasons.append(season_detail)
                else:
                    updated_seasons.append(season)
            except Exception:
                updated_seasons.append(season)

        series.seasons = updated_seasons
        return series

    async def get_series_with_episodes_or_raise(
        self,
        tmdb_id: int,
        language: str | None = None,
        include_episodes: bool = True,
    ) -> TMDBSeries:
        """获取剧集详情；不存在时抛出 TMDBNotFoundError（404）。"""
        series = await self.get_series_with_episodes(
            tmdb_id=tmdb_id,
            language=language,
            include_episodes=include_episodes,
        )
        if series is None:
            raise TMDBNotFoundError("剧集", tmdb_id)
        return series

    async def get_season_or_raise(
        self,
        tmdb_id: int,
        season_number: int,
        language: str | None = None,
    ) -> TMDBSeason:
        """获取季详情；不存在时抛出 TMDBNotFoundError（404）。"""
        season = await self.get_season_by_api(
            tmdb_id=tmdb_id,
            season_number=season_number,
            language=language,
        )
        if season is None:
            raise TMDBNotFoundError("季", f"{tmdb_id}/S{season_number}")
        return season
