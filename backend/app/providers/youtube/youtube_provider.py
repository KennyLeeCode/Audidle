"""YouTube Data API client, used as a popularity signal.

The only module that talks to YouTube. It answers one question: roughly how
widely heard is this song. It is explicitly **not** an audio source. Audidle
never downloads, streams, caches, or plays YouTube audio, and nothing in this
file fetches media.

Quota is the thing that shapes this design. The API bills in units against a
10,000 unit daily default:

    search.list         100 units per call
    videos.list           1 unit per call, up to 50 video ids at a time
    playlistItems.list    1 unit per call

So matching 89 songs by search costs 8,900 units, almost an entire day, while
refreshing the view counts of all 89 afterwards costs 2. That asymmetry drives
everything: search once per song, store the video id forever, and refresh
through the cheap endpoint.

The provider tracks its own spend and refuses to start a call that would run
past the configured budget, so a run stops cleanly and resumably rather than
dying half way through a song.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.core.cache import TtlCache
from app.core.errors import (
    CatalogRateLimitedError,
    CatalogUnavailableError,
    ProviderConfigurationError,
)

logger = logging.getLogger(__name__)

API_BASE = "https://www.googleapis.com/youtube/v3"

# Documented quota costs, used for budgeting rather than guessed at.
COST_SEARCH = 100
COST_VIDEOS = 1
COST_PLAYLIST_ITEMS = 1

# videos.list accepts up to 50 ids for the same single unit, which is what
# makes refreshing an entire catalog almost free.
MAX_VIDEO_IDS_PER_CALL = 50

# Short term rate limiting (429) is separate from daily quota exhaustion (403).
# The first passes on its own, the second does not.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0


class QuotaExhaustedError(CatalogRateLimitedError):
    """The configured YouTube quota budget is spent.

    Its own type because it is not a failure, it is a normal stopping point.
    Callers end the run and report progress rather than retrying.
    """

    code = "youtube_quota_exhausted"


@dataclass(frozen=True)
class YouTubeVideo:
    """A video, as much as we care about it."""

    video_id: str
    title: str
    channel_id: str
    channel_title: str
    duration_ms: int | None = None
    view_count: int | None = None
    published_year: int | None = None
    # Only present on videos.list results, not on search results.
    is_licensed: bool | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


class YouTubePopularityProvider:
    """Searches for a song's video and reads its view count."""

    def __init__(
        self,
        api_key: str,
        daily_quota: int = 10_000,
        quota_reserve: int = 200,
        timeout: float = 15.0,
    ) -> None:
        if not api_key:
            raise ProviderConfigurationError(
                "YOUTUBE_API_KEY must be set to use the YouTube popularity provider"
            )
        self._api_key = api_key
        self._http = httpx.AsyncClient(timeout=timeout)
        self._budget = max(0, daily_quota - quota_reserve)
        self.quota_used = 0
        self.request_count = 0
        # Search results are cached so a retried run does not pay twice for the
        # same query at 100 units a time.
        self._searches: TtlCache[list[YouTubeVideo]] = TtlCache(
            max_entries=2048, ttl_seconds=86_400
        )

    # -- Quota ---------------------------------------------------------------

    @property
    def quota_remaining(self) -> int:
        return max(0, self._budget - self.quota_used)

    def can_afford(self, cost: int) -> bool:
        return self.quota_used + cost <= self._budget

    def _spend(self, cost: int) -> None:
        if not self.can_afford(cost):
            raise QuotaExhaustedError(
                f"YouTube quota budget spent ({self.quota_used} of {self._budget} units). "
                f"Rerun tomorrow, matching resumes where it stopped."
            )
        self.quota_used += cost

    # -- Transport -----------------------------------------------------------

    async def _get(self, path: str, params: dict, cost: int) -> dict:
        """Perform a billed request, retrying short term rate limits.

        Two different limits exist and they are not the same thing. A 403 with
        "quota" in the body means the day's units are gone and the run should
        stop. A 429 means too many requests too quickly, which passes on its own
        after a short wait. Treating the second as fatal, as an earlier version
        did, silently lost eight songs in one run.
        """
        self._spend(cost)
        self.request_count += 1

        for attempt in range(MAX_RETRIES):
            try:
                response = await self._http.get(
                    f"{API_BASE}/{path}", params={**params, "key": self._api_key}
                )
            except httpx.HTTPError as error:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogUnavailableError("Could not reach the YouTube API") from error
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 403:
                body = response.text.lower()
                if "quota" in body:
                    # The server side limit, which is authoritative over our count.
                    self.quota_used = self._budget
                    raise QuotaExhaustedError("YouTube reports the daily quota is exhausted")
                raise ProviderConfigurationError(
                    "YouTube denied the request. Check that the API key is valid and that "
                    "the YouTube Data API v3 is enabled for the project."
                )

            if response.status_code == 429:
                # Google reports the *daily* search quota being gone as a 429
                # with reason rateLimitExceeded, not as the 403 the docs
                # suggest. Retrying that can never succeed, so it has to be
                # told apart from genuine short term throttling by reading the
                # message. Getting this wrong meant every retry burned budget
                # on a request that was already doomed.
                if "quota" in response.text.lower():
                    self.quota_used = self._budget
                    raise QuotaExhaustedError(
                        "YouTube daily search quota is exhausted. It resets at "
                        "midnight Pacific. Matching resumes where it stopped."
                    )

                if attempt == MAX_RETRIES - 1:
                    raise CatalogRateLimitedError(
                        "YouTube is rate limiting requests, try again shortly"
                    )
                wait = float(response.headers.get("Retry-After", 0)) or (
                    RETRY_BACKOFF_SECONDS * (2**attempt)
                )
                logger.warning("YouTube rate limited us, waiting %.1fs", wait)
                await asyncio.sleep(min(wait, 30.0))
                continue

            if response.status_code == 400:
                raise ProviderConfigurationError(
                    f"YouTube rejected the request: {response.text[:200]}"
                )

            if response.status_code >= 500:
                if attempt == MAX_RETRIES - 1:
                    raise CatalogUnavailableError(
                        f"YouTube returned {response.status_code}"
                    )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
                continue

            raise CatalogUnavailableError(
                f"YouTube request to '{path}' failed with {response.status_code}"
            )

        raise CatalogUnavailableError("YouTube did not respond after several attempts")

    # -- Lookups -------------------------------------------------------------

    async def search_song_video(
        self, title: str, artist: str, limit: int = 8
    ) -> list[YouTubeVideo]:
        """Search for candidate videos of a song.

        The expensive call, at 100 units. Callers must check `can_afford` and
        must never call this for a song that already has a stored video id.

        Search results carry no duration or view count, so the candidates come
        back sparse. Callers enrich the shortlist through `hydrate_videos`,
        which costs one unit for up to fifty of them.
        """
        query = f"{artist} {title}"
        cached = self._searches.get(query)
        if cached is not None:
            return cached

        payload = await self._get(
            "search",
            {
                "part": "snippet",
                "q": query,
                "type": "video",
                "maxResults": min(limit, 25),
                # No videoEmbeddable filter. An earlier version passed
                # videoEmbeddable=true to exclude odd formats, which also
                # excluded embedding-restricted uploads. A great many official
                # VEVO music videos are embedding restricted, so the filter was
                # quietly hiding exactly the videos we most want: Lucid Dreams
                # matched a 1.4M view Topic upload because its 1.5B view
                # official video never reached the candidate list.
                #
                # Shorts and other wrong-length uploads are excluded by the
                # duration check during scoring instead, which is where that
                # judgement belongs.
                "videoDuration": "any",
            },
            cost=COST_SEARCH,
        )

        videos = [
            YouTubeVideo(
                video_id=item["id"]["videoId"],
                title=item["snippet"].get("title", ""),
                channel_id=item["snippet"].get("channelId", ""),
                channel_title=item["snippet"].get("channelTitle", ""),
                published_year=_parse_year(item["snippet"].get("publishedAt")),
            )
            for item in payload.get("items", [])
            if item.get("id", {}).get("videoId")
        ]

        self._searches.set(query, videos)
        return videos

    async def hydrate_videos(self, video_ids: list[str]) -> dict[str, YouTubeVideo]:
        """Fetch durations, view counts, and channel details for known videos.

        One unit for up to fifty ids, which is why every other part of this
        design routes through here rather than through search.
        """
        results: dict[str, YouTubeVideo] = {}

        for start in range(0, len(video_ids), MAX_VIDEO_IDS_PER_CALL):
            chunk = video_ids[start : start + MAX_VIDEO_IDS_PER_CALL]
            payload = await self._get(
                "videos",
                {"part": "snippet,contentDetails,statistics", "id": ",".join(chunk)},
                cost=COST_VIDEOS,
            )

            for item in payload.get("items", []):
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                details = item.get("contentDetails", {})

                results[item["id"]] = YouTubeVideo(
                    video_id=item["id"],
                    title=snippet.get("title", ""),
                    channel_id=snippet.get("channelId", ""),
                    channel_title=snippet.get("channelTitle", ""),
                    duration_ms=parse_iso8601_duration(details.get("duration")),
                    # Absent when the uploader hides the count, which is a
                    # normal state and not an error.
                    view_count=_parse_int(statistics.get("viewCount")),
                    published_year=_parse_year(snippet.get("publishedAt")),
                    is_licensed=details.get("licensedContent"),
                )

        return results

    async def get_video_metadata(self, video_id: str) -> YouTubeVideo | None:
        found = await self.hydrate_videos([video_id])
        return found.get(video_id)

    async def get_video_view_count(self, video_id: str) -> int | None:
        video = await self.get_video_metadata(video_id)
        return video.view_count if video else None

    async def refresh_video_stats(self, video_ids: list[str]) -> dict[str, int]:
        """Re-read view counts for videos already matched.

        The cheap path, and the one that runs regularly. Refreshing a thousand
        songs costs twenty units.
        """
        videos = await self.hydrate_videos(video_ids)
        return {
            video_id: video.view_count
            for video_id, video in videos.items()
            if video.view_count is not None
        }

    async def aclose(self) -> None:
        await self._http.aclose()


# -- Parsing ----------------------------------------------------------------


def parse_iso8601_duration(value: str | None) -> int | None:
    """Convert YouTube's ISO 8601 duration into milliseconds.

    Durations arrive as "PT4M33S", "PT1H2M3S", or "PT45S". Parsed by hand
    rather than with a dependency, since the subset in use is small.
    """
    if not value or not value.startswith("PT"):
        return None

    total_seconds = 0
    number = ""
    for char in value[2:]:
        if char.isdigit():
            number += char
        elif char in "HMS" and number:
            amount = int(number)
            total_seconds += amount * {"H": 3600, "M": 60, "S": 1}[char]
            number = ""
        else:
            number = ""

    return total_seconds * 1000 if total_seconds else None


def _parse_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_year(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(str(value)[:4])
    except ValueError:
        return None
